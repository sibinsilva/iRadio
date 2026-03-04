"""iRadio web application – Flask frontend for RadioPlayer."""

import json
import logging
import os
import ssl
import subprocess
import shutil
import threading
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string, request, redirect, url_for

from radio_player import RadioPlayer

# ── Logging ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

APP_VERSION = "2026-03-04-5"
STATIONS_PER_COUNTRY = 50
ALLOWED_CODECS = frozenset({"mp3", "aac", "aac+", "ogg", "opus"})
API_BASE = "https://de1.api.radio-browser.info/json/stations/bycountrycodeexact"
COUNTRY_SOURCES = (
    ("IN", "India"),
    ("IE", "Ireland"),
    ("GB", "UK"),
    ("CA", "Canada"),
    ("US", "USA"),
    ("AE", "Dubai"),
)
FALLBACK_STATIONS = (
    ("Radio Paradise", "https://stream.radioparadise.com/mp3-192"),
    ("SomaFM Groove Salad", "https://ice5.somafm.com/groovesalad-128-mp3"),
    ("KEXP", "https://kexp-mp3-128.streamguys1.com/kexp128.mp3"),
)

# ── Station fetching ─────────────────────────────────────────────────

_ssl_ctx = ssl.create_default_context()


def _fetch_one(country_code: str) -> list[tuple[str, str]]:
    """Fetch up to STATIONS_PER_COUNTRY stations for a country code."""
    url = f"{API_BASE}/{country_code}"
    try:
        with urllib.request.urlopen(url, timeout=8, context=_ssl_ctx) as resp:
            data = json.load(resp)
    except Exception:
        logger.exception("Fetch failed country=%s", country_code)
        return []

    results, seen = [], set()
    for s in data:
        name = (s.get("name") or "").strip()
        stream = (s.get("url_resolved") or s.get("url") or "").strip()
        codec = (s.get("codec") or "").strip().lower()
        if (
            not name
            or not stream
            or not stream.startswith("http")
            or s.get("hls")
            or (codec and codec not in ALLOWED_CODECS)
        ):
            continue
        key = (name.lower(), stream)
        if key in seen:
            continue
        seen.add(key)
        results.append((name, stream))
        if len(results) >= STATIONS_PER_COUNTRY:
            break
    logger.info("Loaded country=%s count=%d", country_code, len(results))
    return results


def _build_stations() -> tuple[dict, dict]:
    """Build radio_stations dict and grouped_stations dict (parallel fetch)."""
    radio_stations: dict[str, dict] = {}
    grouped_stations: dict[str, list[dict]] = {}

    # Fetch all countries in parallel
    with ThreadPoolExecutor(max_workers=len(COUNTRY_SOURCES)) as pool:
        futures = {pool.submit(_fetch_one, code): name for code, name in COUNTRY_SOURCES}

    next_key = 1
    for code, country_name in COUNTRY_SOURCES:
        # Find the matching future
        raw = []
        for fut, name in futures.items():
            if name == country_name:
                raw = fut.result()
                break
        country_list = []
        for station_name, stream_url in raw:
            k = str(next_key)
            radio_stations[k] = {"name": f"{country_name}: {station_name}", "url": stream_url}
            country_list.append({"key": k, "name": station_name, "url": stream_url})
            next_key += 1
        if country_list:
            grouped_stations[country_name] = country_list

    if not radio_stations:
        for i, (name, url) in enumerate(FALLBACK_STATIONS, 1):
            k = str(i)
            radio_stations[k] = {"name": name, "url": url}
        grouped_stations["Fallback"] = [
            {"key": k, "name": v["name"], "url": v["url"]}
            for k, v in radio_stations.items()
        ]

    logger.info("Total stations=%d", len(radio_stations))
    return radio_stations, grouped_stations


radio_stations, grouped_stations = _build_stations()

# ── Player state ─────────────────────────────────────────────────────

player = RadioPlayer()
current_station_name: str | None = None

# ── HTML template ────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>iRadio Player</title>
<style>
:root{--bg:#0f172a;--card:#111827;--text:#e5e7eb;--muted:#94a3b8;--accent:#38bdf8;--accent-dk:#0ea5e9;--danger:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",Tahoma,Geneva,Verdana,sans-serif;background:radial-gradient(1200px 600px at 10% -10%,#1e293b,var(--bg));color:var(--text);min-height:100vh}
.c{max-width:980px;margin:0 auto;padding:24px}
.hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.hdr h1{font-size:28px;font-weight:700}
.ver{font-size:12px;color:var(--muted)}
.now{background:linear-gradient(180deg,#111827,#0b1220);border:1px solid #1f2937;border-radius:14px;padding:16px;display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.3)}
.now b{display:block;font-size:15px}
.now small{color:var(--muted);font-size:13px}
.btn{background:var(--accent);color:#032b3f;border:none;padding:10px 16px;border-radius:10px;font-weight:700;cursor:pointer;transition:transform .08s,background .2s}
.btn:hover{background:var(--accent-dk)}
.btn:active{transform:translateY(1px)}
.btn-stop{background:var(--danger);color:#fff}
.sec{margin:18px 0 10px;font-size:16px;color:var(--muted);font-weight:600}
.cb{margin-bottom:12px}
.ch{display:block;width:100%;text-align:left;background:#0b1220;border:1px solid #1f2937;padding:12px 14px;border-radius:10px;color:var(--text);font-size:16px;font-weight:700;cursor:pointer;user-select:none}
.ch:hover{background:#1e293b}
.cc{margin-top:10px;display:none}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.cd{background:var(--card);border:1px solid #1f2937;border-radius:14px;padding:14px;display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:72px}
.cd span{font-weight:600;line-height:1.2}
a{text-decoration:none}
</style>
</head>
<body>
<div class="c">
  <div class="hdr"><h1>iRadio Player</h1><span class="ver">v{{ v }}</span></div>
  <div class="now">
    <div>
      {% if np %}<b>Now Playing: {{ np }}</b>{% else %}<b>No station playing</b>{% endif %}
      {% if st.error %}<small style="color:var(--danger)">{{ st.error }}</small>
      {% elif st.state %}<small>{{ st.state }}</small>{% endif %}
    </div>
    <form action="/stop" method="post"><button class="btn btn-stop">Stop</button></form>
  </div>
  <div class="sec">Available Radio Stations</div>
  {% for country, items in gs.items() %}
  <div class="cb">
    <div class="ch" onclick="var e=this.nextElementSibling;e.style.display=e.style.display==='block'?'none':'block'">{{ country }} ({{ items|length }})</div>
    <div class="cc"><div class="g">
      {% for s in items %}
      <div class="cd"><span>{{ s.name }}</span><a href="/play/{{ s.key }}"><button class="btn">Play</button></a></div>
      {% endfor %}
    </div></div>
  </div>
  {% endfor %}
</div>
</body>
</html>"""

# ── Flask app ────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        gs=grouped_stations,
        np=current_station_name if player.is_playing() else None,
        st=player.status(),
        v=APP_VERSION,
    )


@app.route("/play/<key>")
def play_station(key):
    global current_station_name
    if key not in radio_stations:
        return "Invalid station", 404
    station = radio_stations[key]
    current_station_name = station["name"]
    threading.Thread(target=player.play, args=(station["url"],), daemon=True).start()
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    global current_station_name
    player.stop()
    current_station_name = None
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return {"status": "ok", "stations": len(radio_stations), "version": APP_VERSION}


# ── Entry point ──────────────────────────────────────────────────────

def run():
    url = "http://127.0.0.1:5000/"

    def _open():
        if os.name == "nt" and os.environ.get("IRADIO_OPEN_IE") == "1":
            ie = shutil.which("iexplore")
            if ie:
                try:
                    subprocess.Popen([ie, url])
                    return
                except Exception:
                    pass
        webbrowser.open(url)

    threading.Timer(1.0, _open).start()
    app.run(use_reloader=False, threaded=True)


if __name__ == "__main__":
    run()
