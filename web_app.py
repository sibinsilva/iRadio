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

APP_VERSION = "2026-03-04-8"
ALLOWED_CODECS = frozenset({"mp3", "aac", "aac+", "ogg", "opus"})
API_BASE = "https://de1.api.radio-browser.info/json/stations/bycountrycodeexact"
COUNTRY_SOURCES = (
    ("US", "USA", "🇺🇸"),
    ("GB", "United Kingdom", "🇬🇧"),
    ("DE", "Germany", "🇩🇪"),
    ("FR", "France", "🇫🇷"),
    ("IN", "India", "🇮🇳"),
    ("CA", "Canada", "🇨🇦"),
    ("IE", "Ireland", "🇮🇪"),
    ("AU", "Australia", "🇦🇺"),
    ("ES", "Spain", "🇪🇸"),
    ("IT", "Italy", "🇮🇹"),
    ("BR", "Brazil", "🇧🇷"),
    ("AE", "Dubai / UAE", "🇦🇪"),
    ("JP", "Japan", "🇯🇵"),
    ("NL", "Netherlands", "🇳🇱"),
)
FALLBACK_STATIONS = (
    ("Radio Paradise", "https://stream.radioparadise.com/mp3-192"),
    ("SomaFM Groove Salad", "https://ice5.somafm.com/groovesalad-128-mp3"),
    ("KEXP", "https://kexp-mp3-128.streamguys1.com/kexp128.mp3"),
)

# ── Station fetching ─────────────────────────────────────────────────

_ssl_ctx = ssl.create_default_context()


def _fetch_one(country_code: str) -> list[dict]:
    """Fetch up to STATIONS_PER_COUNTRY stations for a country code."""
    url = f"{API_BASE}/{country_code}?order=votes&reverse=true"
    try:
        with urllib.request.urlopen(url, timeout=8, context=_ssl_ctx) as resp:
            data = json.load(resp)
    except Exception:
        logger.exception("Fetch failed country=%s", country_code)
        return []

    results, seen_names, seen_streams = [], set(), set()
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

        name_lower = name.lower()
        stream_norm = stream.lower().rstrip("/")

        if name_lower in seen_names or stream_norm in seen_streams:
            continue

        seen_names.add(name_lower)
        seen_streams.add(stream_norm)

        # Get tags (genres) - split and take first 2
        raw_tags = s.get("tags") or ""
        tags = [t.strip().capitalize() for t in raw_tags.split(",") if t.strip()][:2]

        # Get bitrate
        bitrate = s.get("bitrate") or 0

        # Get favicon
        favicon = (s.get("favicon") or "").strip()

        results.append({
            "name": name,
            "url": stream,
            "codec": codec.upper(),
            "bitrate": f"{bitrate}k" if bitrate else "",
            "tags": tags,
            "favicon": favicon
        })
    logger.info("Loaded country=%s count=%d", country_code, len(results))
    return results


# ── Initialization ───────────────────────────────────────────────────

player = RadioPlayer()
current_station_name: str | None = None
next_station_key = 1
radio_stations: dict[str, dict] = {}
grouped_stations: dict[str, list[dict]] = {}

import time
sleep_timer: threading.Timer | None = None
sleep_timer_end_time: float | None = None


def _stop_from_timer():
    global sleep_timer, sleep_timer_end_time
    logger.info("Sleep timer expired. Stopping playback.")
    player.stop()
    sleep_timer = None
    sleep_timer_end_time = None


# 1. Load Featured Fallback stations immediately (no startup network block)
featured_list = []
for name, url in FALLBACK_STATIONS:
    k = str(next_station_key)
    radio_stations[k] = {"name": f"Featured: {name}", "url": url, "favicon": ""}
    featured_list.append({
        "key": k,
        "name": name,
        "codec": "MP3",
        "bitrate": "192k",
        "tags": ["Featured", "High-Quality"],
        "favicon": ""
    })
    next_station_key += 1
grouped_stations["Featured"] = featured_list

# ── HTML template ────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>iRadio Player</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;700;800&family=JetBrains+Mono:wght@400;700&display=swap');

:root {
  --bg: #0b0914;
  --panel: rgba(22, 16, 38, 0.45);
  --panel-solid: #151024;
  --card: rgba(255, 255, 255, 0.02);
  --card-hover: rgba(255, 255, 255, 0.05);
  --border: rgba(255, 255, 255, 0.06);
  --border-glow: rgba(6, 182, 212, 0.15);
  --text: #f1f5f9;
  --muted: #94a3b8;
  --accent: #06b6d4;
  --accent-hover: #0891b2;
  --accent-glow: rgba(6, 182, 212, 0.35);
  --danger: #f43f5e;
  --danger-hover: #e11d48;
  --star: #fbbf24;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Inter', sans-serif;
  background: radial-gradient(circle at 50% 0%, #22143d 0%, var(--bg) 80%);
  color: var(--text);
  min-height: 100vh;
  padding-bottom: 120px;
  overflow-y: scroll;
}

/* Custom Scrollbar */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.08); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent); }

.app-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  min-height: 100vh;
  width: 100%;
}

.sidebar {
  background: rgba(11, 8, 20, 0.4);
  backdrop-filter: blur(25px);
  -webkit-backdrop-filter: blur(25px);
  border-right: 1px solid var(--border);
  padding: 32px 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  position: sticky;
  top: 0;
  height: calc(100vh - 120px);
  overflow-y: auto;
}

.main-content {
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  overflow-y: auto;
  max-width: 1100px;
}

/* Header inside sidebar */
.hdr {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
}
.hdr h1 {
  font-family: 'Outfit', sans-serif;
  font-size: 28px;
  font-weight: 800;
  background: linear-gradient(135deg, #fff 0%, #c7d2fe 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  letter-spacing: -0.5px;
  margin: 0;
}
.ver {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  background: rgba(255, 255, 255, 0.04);
  padding: 3px 6px;
  border-radius: 6px;
  color: var(--muted);
  border: 1px solid var(--border);
  display: inline-block;
}

/* Country Selector List inside sidebar */
.country-grid-vertical {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.country-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 16px;
  color: var(--text);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: all 0.2s ease;
  backdrop-filter: blur(10px);
  text-align: left;
  width: 100%;
}
.country-card:hover {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.12);
  transform: translateX(2px);
}
.country-card.active {
  background: rgba(6, 182, 212, 0.12);
  border-color: rgba(6, 182, 212, 0.4);
  box-shadow: 0 0 10px rgba(6, 182, 212, 0.15);
  color: #fff;
}
.country-flag {
  font-size: 20px;
  line-height: 1;
}
.country-name {
  font-family: 'Outfit', sans-serif;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}

/* Sleep Timer Styles */
.sleep-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 700;
  background: var(--accent);
  color: #032b3f;
  padding: 1px 4px;
  border-radius: 4px;
  margin-left: 6px;
  display: inline-block;
  vertical-align: middle;
}
.sleep-btn.sleep-active {
  color: var(--accent) !important;
}

/* Control Hub / Now Playing Bar */
.control-hub {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  width: calc(100% - 48px);
  max-width: 932px;
  background: rgba(11, 8, 20, 0.85);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 20px;
  padding: 16px 24px;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 24px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5), 0 0 20px var(--border-glow);
  z-index: 1000;
  transition: border-color 0.3s;
}
.control-hub.playing-active {
  border-color: rgba(6, 182, 212, 0.3);
}

.now-playing-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.now-playing-info b {
  font-family: 'Outfit', sans-serif;
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hub-song {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: -2px;
  font-family: 'Inter', sans-serif;
}
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
  display: inline-block;
}
.status-dot.playing { background: var(--accent); box-shadow: 0 0 8px var(--accent); }
.status-dot.buffering { background: #eab308; box-shadow: 0 0 8px #eab308; animation: pulse 1s infinite alternate; }
.status-dot.error { background: var(--danger); box-shadow: 0 0 8px var(--danger); }

/* Equalizer CSS Wave */
.eq {
  display: none;
  align-items: flex-end;
  gap: 3px;
  height: 16px;
  width: 25px;
}
.eq.active { display: flex; }
.eq-bar {
  width: 3px;
  height: 100%;
  background-color: var(--accent);
  border-radius: 1px;
  transform-origin: bottom;
  animation: bounce 0.8s ease infinite alternate;
}
.eq-bar:nth-child(2) { animation-delay: 0.15s; }
.eq-bar:nth-child(3) { animation-delay: 0.3s; }
.eq-bar:nth-child(4) { animation-delay: 0.45s; }

@keyframes bounce {
  0% { transform: scaleY(0.15); }
  100% { transform: scaleY(1); }
}
@keyframes pulse {
  0% { opacity: 0.4; }
  100% { opacity: 1; }
}

/* Play/Stop Button */
.hub-controls {
  display: flex;
  justify-content: center;
}
.btn {
  background: var(--accent);
  color: #032b3f;
  border: none;
  padding: 10px 20px;
  border-radius: 12px;
  font-weight: 700;
  font-size: 14px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 4px 12px rgba(6, 182, 212, 0.2);
}
.btn:hover {
  background: var(--accent-hover);
  transform: translateY(-1px);
  box-shadow: 0 6px 16px var(--accent-glow);
}
.btn:active { transform: translateY(1px); }
.btn-stop {
  background: var(--danger);
  color: #fff;
  box-shadow: 0 4px 12px rgba(244, 63, 94, 0.2);
}
.btn-stop:hover {
  background: var(--danger-hover);
  box-shadow: 0 6px 16px rgba(244, 63, 94, 0.4);
}
.btn svg { fill: currentColor; }

/* Volume Slider */
.volume-control {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}
.volume-slider {
  -webkit-appearance: none;
  width: 100px;
  height: 6px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
  outline: none;
  cursor: pointer;
}
.volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent);
  transition: background 0.15s;
}
.volume-slider::-webkit-slider-thumb:hover {
  background: #fff;
}
.vol-btn {
  background: transparent;
  border: none;
  color: var(--muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  transition: color 0.15s;
}
.vol-btn:hover { color: var(--text); }
.vol-btn svg { fill: currentColor; }

/* Search Bar */
.search-box {
  position: relative;
  margin-bottom: 24px;
}
.search-input {
  width: 100%;
  background: rgba(17, 12, 28, 0.5);
  backdrop-filter: blur(10px);
  border: 1px solid var(--border);
  padding: 14px 16px 14px 44px;
  border-radius: 12px;
  color: #fff;
  font-size: 15px;
  font-family: inherit;
  outline: none;
  transition: all 0.2s;
}
.search-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 12px var(--border-glow);
  background: rgba(17, 12, 28, 0.8);
}
.search-icon {
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--muted);
  display: flex;
  align-items: center;
}
.search-icon svg { fill: currentColor; }

/* Sections */
.sec {
  font-family: 'Outfit', sans-serif;
  margin: 28px 0 14px;
  font-size: 20px;
  color: #fff;
  font-weight: 700;
  letter-spacing: -0.3px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

/* Category Collapse Block */
.cb {
  margin-bottom: 20px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 16px;
  overflow: hidden;
  backdrop-filter: blur(10px);
  transition: border-color 0.2s;
}
.ch {
  width: 100%;
  text-align: left;
  background: transparent;
  border: none;
  padding: 16px 20px;
  color: var(--text);
  font-family: 'Outfit', sans-serif;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  user-select: none;
}
.ch-arrow {
  width: 10px;
  height: 10px;
  border-bottom: 2px solid var(--muted);
  border-right: 2px solid var(--muted);
  transform: rotate(45deg);
  transition: transform 0.2s;
  margin-right: 4px;
}
.cb.expanded .ch-arrow {
  transform: rotate(-135deg);
}
.cc {
  padding: 0 20px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.03);
}

/* Grid & Cards */
.g {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 14px;
  padding-top: 8px;
}
.cd {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 14px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 72px;
  transition: all 0.2s ease;
}
.cd:hover {
  background: var(--card-hover);
  border-color: rgba(255, 255, 255, 0.12);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
}
.cd.active {
  background: rgba(6, 182, 212, 0.05);
  border-color: rgba(6, 182, 212, 0.3);
  box-shadow: 0 0 15px rgba(6, 182, 212, 0.1);
}
.cd-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}
.cd-avatar-container {
  width: 44px;
  height: 44px;
  min-width: 44px;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: center;
}
.cd-avatar {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cd-avatar-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'Outfit', sans-serif;
  font-size: 16px;
  font-weight: 800;
  color: #fff;
  text-shadow: 0 1px 2px rgba(0,0,0,0.3);
}
.cd-info span {
  font-weight: 600;
  font-size: 14px;
  line-height: 1.3;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cd-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.06);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--muted);
}
.badge.codec {
  background: rgba(6, 182, 212, 0.1);
  color: var(--accent);
  border-color: rgba(6, 182, 212, 0.15);
}
.tag-badge {
  font-size: 10px;
  color: var(--muted);
  background: rgba(255,255,255,0.02);
  padding: 1px 5px;
  border-radius: 4px;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Icons */
.icon-btn {
  background: transparent;
  border: none;
  color: var(--muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  border-radius: 8px;
  transition: all 0.15s;
}
.icon-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.05);
}
.star-btn:hover { color: var(--star); }
.star-btn.fav { color: var(--star); }
.star-btn svg { fill: currentColor; }

.play-card-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s;
}
.play-card-btn:hover {
  background: var(--accent);
  color: #032b3f;
  transform: scale(1.08);
}
.cd.active .play-card-btn {
  background: var(--accent);
  color: #032b3f;
}
.play-card-btn svg { fill: currentColor; width: 14px; height: 14px; }

/* Favorites List styling */
#fav-block {
  display: none;
}
#fav-block.has-favs {
  display: block;
}

/* Skeleton Loading Animation */
.skeleton-card {
  height: 72px;
  background: rgba(255, 255, 255, 0.01);
  border: 1px solid var(--border);
  border-radius: 14px;
  position: relative;
  overflow: hidden;
}
.skeleton-card::after {
  content: "";
  display: block;
  width: 100%;
  height: 100%;
  position: absolute;
  top: 0; left: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.03), transparent);
  animation: loading 1.5s infinite;
}
@keyframes loading {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}

@media(max-width: 768px) {
  .app-layout {
    grid-template-columns: 1fr;
  }
  .sidebar {
    position: relative;
    height: auto;
    border-right: none;
    border-bottom: 1px solid var(--border);
    padding: 20px;
  }
  .main-content {
    padding: 20px;
  }
  .country-grid-vertical {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 8px;
  }
  .country-card {
    width: auto;
    flex: 1 1 calc(33.33% - 8px);
    min-width: 110px;
    justify-content: center;
    padding: 10px;
  }
  .control-hub {
    grid-template-columns: 1fr;
    gap: 12px;
    padding: 16px;
    text-align: center;
  }
  .now-playing-info {
    align-items: center;
  }
  .volume-control {
    justify-content: center;
  }
  .hub-controls {
    order: -1;
  }
}
</style>
</head>
<body>
<div class="app-layout">
  <!-- Left Sidebar -->
  <div class="sidebar">
    <div class="hdr">
      <h1>iRadio Player</h1>
      <span class="ver">v{{ v }}</span>
    </div>

    <div class="sec">Countries</div>
    <div class="country-grid-vertical">
      <button class="country-card active" id="btn-featured" onclick="loadFeatured()">
        <span class="country-flag">⭐</span>
        <span class="country-name">Featured</span>
      </button>
      {% for code, name, flag in cs %}
      <button class="country-card" id="btn-{{ code }}" onclick="loadCountry('{{ code }}', '{{ flag }} {{ name }}')">
        <span class="country-flag">{{ flag }}</span>
        <span class="country-name">{{ name }}</span>
      </button>
      {% endfor %}
    </div>
  </div>

  <!-- Right Content Area -->
  <div class="main-content">
    <!-- Favorites Section -->
    <div id="fav-block" class="cb expanded">
      <div class="ch" onclick="toggleCollapse(this.parentElement)">
        <span>🌟 Starred Stations</span>
        <div class="ch-arrow"></div>
      </div>
      <div class="cc">
        <div class="g" id="favorites-grid">
          <!-- Starred cards injected dynamically by JS -->
        </div>
      </div>
    </div>

    <!-- Search Bar -->
    <div class="search-box">
      <div class="search-icon">
        <svg width="18" height="18" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
      </div>
      <input type="text" class="search-input" id="search" placeholder="Search within current list..." onkeyup="filterStations()"/>
    </div>

    <!-- Station Panel Header -->
    <div class="sec">
      <span id="country-title">Featured Stations</span>
    </div>

    <!-- Main Dynamic Stations Grid -->
    <div class="cb expanded">
      <div class="cc">
        <div class="g" id="stations-grid">
          <!-- Station cards rendered dynamically -->
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Bottom Control Hub / Dock -->
<div class="control-hub" id="hub">
  <!-- Info -->
  <div class="now-playing-info">
    <b id="hub-title">No station playing</b>
    <span id="hub-song" class="hub-song" style="display: none;"></span>
    <div class="status-badge">
      <div class="status-dot" id="status-dot"></div>
      <span id="status-text">Stopped</span>
      <!-- CSS Equalizer Wave -->
      <div class="eq" id="equalizer">
        <div class="eq-bar"></div>
        <div class="eq-bar"></div>
        <div class="eq-bar"></div>
        <div class="eq-bar"></div>
      </div>
    </div>
  </div>

  <!-- Main Action -->
  <div class="hub-controls">
    <button class="btn btn-stop" id="stop-btn" onclick="stopPlayer()" style="display: none;">
      <svg width="14" height="14" viewBox="0 0 24 24"><path d="M6 19h12V5H6v14z"/></svg>
      Stop Playback
    </button>
  </div>

  <!-- Volume & Sleep Controls -->
  <div class="volume-control">
    <button class="icon-btn sleep-btn" id="sleep-btn" onclick="cycleSleepTimer()" title="Set Sleep Timer (Off -> 15m -> 30m -> 45m -> 60m)">
      <svg width="18" height="18" viewBox="0 0 24 24"><path fill="currentColor" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 11H7v-2h4V7h2v6z"/></svg>
      <span id="sleep-badge" class="sleep-badge" style="display: none;"></span>
    </button>
    <button class="vol-btn" onclick="toggleMute()" id="mute-btn" title="Mute/Unmute">
      <svg width="20" height="20" viewBox="0 0 24 24" id="volume-icon"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"/></svg>
    </button>
    <input type="range" class="volume-slider" min="0" max="100" value="80" id="volume" oninput="changeVolume(this.value)" title="Volume Slider"/>
  </div>
</div>

<script>
// Initial static stations table (Featured)
const STATIONS = {
  {% for k, s in rs.items() %}
  "{{ k }}": { name: {{ s.name|tojson }}, url: {{ s.url|tojson }}, favicon: {{ (s.get("favicon") or "")|tojson }} },
  {% endfor %}
};

// Internal client caches
const countryCache = {
  "FEATURED": [
    {% for s in gs["Featured"] %}
    {
      key: "{{ s.key }}",
      name: {{ s.name|tojson }},
      codec: "{{ s.codec }}",
      bitrate: "{{ s.bitrate }}",
      tags: {{ s.tags|tojson }},
      favicon: "{{ s.favicon }}"
    },
    {% endfor %}
  ]
};

let currentPlayingKey = null;
let savedVolume = 80;
let isMuted = false;
let currentList = [...countryCache["FEATURED"]];

// Fallback avatar handling
function handleAvatarError(img, name) {
  img.onerror = null;
  const container = img.parentElement;
  if (!container) return;
  container.innerHTML = '';
  
  const initials = document.createElement('div');
  initials.className = 'cd-avatar-fallback';
  
  const cleanName = name.replace('Featured: ', '').trim();
  const letter = cleanName ? cleanName.charAt(0).toUpperCase() : '📻';
  initials.innerText = letter;
  
  const colors = [
    'linear-gradient(135deg, #ec4899, #8b5cf6)',
    'linear-gradient(135deg, #3b82f6, #06b6d4)',
    'linear-gradient(135deg, #10b981, #3b82f6)',
    'linear-gradient(135deg, #f59e0b, #ef4444)',
    'linear-gradient(135deg, #8b5cf6, #ec4899)'
  ];
  const charCode = letter.charCodeAt(0) || 0;
  const colorIdx = charCode % colors.length;
  initials.style.background = colors[colorIdx];
  container.appendChild(initials);
}

// Sleep Timer variables & functions
const sleepOptions = [0, 15, 30, 45, 60];
let currentSleepOptionIdx = 0;

function cycleSleepTimer() {
  currentSleepOptionIdx = (currentSleepOptionIdx + 1) % sleepOptions.length;
  const minutes = sleepOptions[currentSleepOptionIdx];
  
  fetch(`/api/sleep/${minutes}`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      updateSleepUI(data.remaining);
    })
    .catch(err => console.error("Error setting sleep timer:", err));
}

function updateSleepUI(remainingSeconds) {
  const badge = document.getElementById('sleep-badge');
  const btn = document.getElementById('sleep-btn');
  if (!badge || !btn) return;
  
  if (remainingSeconds > 0) {
    badge.style.display = 'inline-block';
    const mins = Math.ceil(remainingSeconds / 60);
    badge.innerText = `${mins}m`;
    btn.classList.add('sleep-active');
  } else {
    badge.style.display = 'none';
    badge.innerText = '';
    btn.classList.remove('sleep-active');
  }
}

// Load and Render Favorites on Startup
function getFavorites() {
  try {
    return JSON.parse(localStorage.getItem('iradio_favs') || '[]');
  } catch(e) {
    return [];
  }
}

function saveFavorites(favs) {
  localStorage.setItem('iradio_favs', JSON.stringify(favs));
}

function renderFavorites() {
  const favs = getFavorites();
  const grid = document.getElementById('favorites-grid');
  const block = document.getElementById('fav-block');
  
  grid.innerHTML = '';
  
  if (favs.length === 0) {
    block.classList.remove('has-favs');
    return;
  }
  
  block.classList.add('has-favs');
  
  favs.forEach(key => {
    const s = STATIONS[key];
    if (!s) return;
    
    const isActive = (key === currentPlayingKey);
    
    const card = document.createElement('div');
    card.className = `cd ${isActive ? 'active' : ''}`;
    card.id = `fav-card-${key}`;
    card.innerHTML = `
      <div class="cd-avatar-container">
        <img class="cd-avatar" src="${s.favicon || ''}" onerror="handleAvatarError(this, '${s.name}')" loading="lazy" alt=""/>
      </div>
      <div class="cd-info">
        <span>${s.name}</span>
        <div class="cd-meta">
          <span class="badge">FAVORITE</span>
        </div>
      </div>
      <div class="card-actions">
        <button class="icon-btn star-btn fav" onclick="toggleFavorite('${key}', event)" title="Remove Favorite">
          <svg width="18" height="18" viewBox="0 0 24 24"><path d="M12 .587l3.668 7.431 8.2 1.191-5.934 5.787 1.4 8.168L12 18.896l-7.334 3.857 1.4-8.168L.132 9.209l8.2-1.191L12 .587z"/></svg>
        </button>
        <button class="play-card-btn" onclick="playStation('${key}')">
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
        </button>
      </div>
    `;
    grid.appendChild(card);
  });
}

function toggleFavorite(key, event) {
  if (event) event.stopPropagation();
  let favs = getFavorites();
  const idx = favs.indexOf(key);
  
  if (idx > -1) {
    favs.splice(idx, 1);
  } else {
    favs.push(key);
  }
  
  saveFavorites(favs);
  renderFavorites();
  updateStarIcons();
}

function updateStarIcons() {
  const favs = getFavorites();
  document.querySelectorAll('.star-btn').forEach(btn => btn.classList.remove('fav'));
  favs.forEach(key => {
    const card = document.getElementById(`card-${key}`);
    if (card) {
      const star = card.querySelector('.star-btn');
      if (star) star.classList.add('fav');
    }
  });
}

function toggleCollapse(el) { el.classList.toggle('expanded'); }

function playStation(key) {
  currentPlayingKey = key;
  const s = STATIONS[key];
  if (!s) return;

  document.getElementById('hub-title').innerText = s.name.replace('Featured: ', '');
  updateStatusUI('starting', null);
  updateActiveCards(key);

  fetch(`/play/${key}?ajax=1`)
    .then(r => r.json())
    .then(data => { pollStatus(); })
    .catch(err => console.error("Error starting stream:", err));
}

function stopPlayer() {
  fetch('/stop?ajax=1', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      currentPlayingKey = null;
      document.getElementById('hub-title').innerText = "No station playing";
      updateStatusUI('stopped', null);
      updateActiveCards(null);
    })
    .catch(err => console.error("Error stopping player:", err));
}

function changeVolume(val) {
  if (isMuted && val > 0) {
    isMuted = false;
    updateVolumeIcon(val);
  }
  savedVolume = val;
  fetch(`/volume/${val}`, { method: 'POST' })
    .catch(err => console.error("Error setting volume:", err));
}

function toggleMute() {
  const slider = document.getElementById('volume');
  if (isMuted) {
    isMuted = false;
    slider.value = savedVolume;
    changeVolume(savedVolume);
  } else {
    isMuted = true;
    slider.value = 0;
    fetch('/volume/0', { method: 'POST' })
      .then(() => updateVolumeIcon(0))
      .catch(err => console.error("Error setting mute:", err));
  }
}

function updateVolumeIcon(val) {
  const path = document.getElementById('volume-icon').querySelector('path');
  if (val == 0) {
    path.setAttribute('d', 'M4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z');
  } else if (val < 50) {
    path.setAttribute('d', 'M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z');
  } else {
    path.setAttribute('d', 'M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z');
  }
}

function updateActiveCards(activeKey) {
  document.querySelectorAll('.cd').forEach(c => c.classList.remove('active'));
  
  if (activeKey) {
    const mainCard = document.getElementById(`card-${activeKey}`);
    if (mainCard) mainCard.classList.add('active');
    
    const favCard = document.getElementById(`fav-card-${activeKey}`);
    if (favCard) favCard.classList.add('active');
  }
}

function updateStatusUI(state, error) {
  const dot = document.getElementById('status-dot');
  const txt = document.getElementById('status-text');
  const eq = document.getElementById('equalizer');
  const stopBtn = document.getElementById('stop-btn');
  const hub = document.getElementById('hub');

  dot.className = 'status-dot';
  eq.classList.remove('active');
  hub.classList.remove('playing-active');

  if (state === 'playing') {
    dot.classList.add('playing');
    txt.innerText = 'Playing';
    eq.classList.add('active');
    hub.classList.add('playing-active');
    stopBtn.style.display = 'inline-flex';
  } else if (state === 'buffering' || state === 'starting') {
    dot.classList.add('buffering');
    txt.innerText = state === 'starting' ? 'Starting...' : 'Buffering...';
    stopBtn.style.display = 'inline-flex';
  } else if (state === 'error') {
    dot.classList.add('error');
    txt.innerText = error || 'Error';
    stopBtn.style.display = 'inline-flex';
  } else {
    txt.innerText = 'Stopped';
    stopBtn.style.display = 'none';
  }
}

function pollStatus() {
  fetch('/status')
    .then(r => r.json())
    .then(data => {
      if (data.state) {
        updateStatusUI(data.state, data.error);
        
        // Sync active state keys
        if (data.state === 'playing' || data.state === 'buffering' || data.state === 'starting') {
          if (data.current_station) {
            let matchedKey = null;
            for (let k in STATIONS) {
              if (STATIONS[k].name === data.current_station || STATIONS[k].name === `Featured: ${data.current_station}`) {
                matchedKey = k;
                break;
              }
            }
            if (matchedKey && matchedKey !== currentPlayingKey) {
              currentPlayingKey = matchedKey;
              document.getElementById('hub-title').innerText = STATIONS[matchedKey].name.replace('Featured: ', '');
              updateActiveCards(matchedKey);
            }
          }
          const songText = document.getElementById('hub-song');
          if (data.now_playing) {
            songText.innerText = data.now_playing;
            songText.style.display = 'block';
          } else {
            songText.style.display = 'none';
            songText.innerText = '';
          }
        } else {
          if (currentPlayingKey !== null) {
            currentPlayingKey = null;
            document.getElementById('hub-title').innerText = "No station playing";
            updateActiveCards(null);
          }
          const songText = document.getElementById('hub-song');
          songText.style.display = 'none';
          songText.innerText = '';
        }
      }
      
      // Sync Volume
      if (data.volume !== undefined) {
        const slider = document.getElementById('volume');
        if (document.activeElement !== slider) {
          slider.value = data.volume;
          updateVolumeIcon(data.volume);
          if (!isMuted) savedVolume = data.volume;
        }
      }
      
      // Sync Sleep Timer
      if (data.sleep_remaining !== undefined) {
        updateSleepUI(data.sleep_remaining);
        if (data.sleep_remaining > 0) {
          const mins = Math.ceil(data.sleep_remaining / 60);
          const optMins = [15, 30, 45, 60];
          let nearestIdx = optMins.findIndex(m => m >= mins) + 1;
          currentSleepOptionIdx = nearestIdx > 0 ? nearestIdx : 0;
        } else {
          currentSleepOptionIdx = 0;
        }
      }
    })
    .catch(err => console.error("Error polling player status:", err));
}

function renderStations(stations) {
  const grid = document.getElementById('stations-grid');
  grid.innerHTML = '';
  
  if (stations.length === 0) {
    grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--muted); padding: 40px;">No stations found for this category.</div>';
    return;
  }

  const query = document.getElementById('search').value.toLowerCase().trim();
  
  if (query !== '' || stations.length <= 10) {
    renderCardList(grid, stations);
    return;
  }
  
  const top10 = stations.slice(0, 10);
  const remaining = stations.slice(10);
  
  renderCardList(grid, top10);
  
  const remContainer = document.createElement('div');
  remContainer.id = 'remaining-container';
  remContainer.className = 'g';
  remContainer.style.display = 'none';
  remContainer.style.gridColumn = '1 / -1';
  renderCardList(remContainer, remaining);
  grid.appendChild(remContainer);
  
  const btnContainer = document.createElement('div');
  btnContainer.style.gridColumn = '1 / -1';
  btnContainer.style.display = 'flex';
  btnContainer.style.justifyContent = 'center';
  btnContainer.style.margin = '20px 0 10px';
  
  const showMoreBtn = document.createElement('button');
  showMoreBtn.className = 'btn';
  showMoreBtn.id = 'show-more-btn';
  showMoreBtn.style.background = 'rgba(255, 255, 255, 0.04)';
  showMoreBtn.style.border = '1px solid var(--border)';
  showMoreBtn.style.color = 'var(--text)';
  showMoreBtn.innerHTML = `Show More Stations (+${remaining.length})`;
  
  showMoreBtn.onclick = () => {
    if (remContainer.style.display === 'none') {
      remContainer.style.display = 'grid';
      showMoreBtn.innerHTML = 'Show Less Stations';
    } else {
      remContainer.style.display = 'none';
      showMoreBtn.innerHTML = `Show More Stations (+${remaining.length})`;
    }
  };
  
  btnContainer.appendChild(showMoreBtn);
  grid.appendChild(btnContainer);
}

function renderCardList(container, list) {
  list.forEach(s => {
    const isActive = (s.key === currentPlayingKey);
    const card = document.createElement('div');
    card.className = `cd ${isActive ? 'active' : ''}`;
    card.id = `card-${s.key}`;
    
    let badgesHtml = '';
    if (s.codec) badgesHtml += `<span class="badge codec">${s.codec}</span>`;
    if (s.bitrate) badgesHtml += `<span class="badge">${s.bitrate}</span>`;
    if (s.tags && s.tags.length > 0) {
      s.tags.forEach(t => {
        badgesHtml += `<span class="tag-badge">${t}</span>`;
      });
    }
    
    card.innerHTML = `
      <div class="cd-avatar-container">
        <img class="cd-avatar" src="${s.favicon || ''}" onerror="handleAvatarError(this, '${s.name}')" loading="lazy" alt=""/>
      </div>
      <div class="cd-info">
        <span>${s.name}</span>
        <div class="cd-meta">
          ${badgesHtml}
        </div>
      </div>
      <div class="card-actions">
        <button class="icon-btn star-btn" onclick="toggleFavorite('${s.key}', event)" title="Star station">
          <svg width="18" height="18" viewBox="0 0 24 24"><path d="M12 .587l3.668 7.431 8.2 1.191-5.934 5.787 1.4 8.168L12 18.896l-7.334 3.857 1.4-8.168L.132 9.209l8.2-1.191L12 .587z"/></svg>
        </button>
        <button class="play-card-btn" onclick="playStation('${s.key}')">
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
        </button>
      </div>
    `;
    container.appendChild(card);
  });
  updateStarIcons();
}

function showLoadingSkeleton() {
  const grid = document.getElementById('stations-grid');
  grid.innerHTML = '';
  for(let i=0; i<6; i++) {
    const sk = document.createElement('div');
    sk.className = 'skeleton-card';
    grid.appendChild(sk);
  }
}

function loadFeatured() {
  document.querySelectorAll('.country-card').forEach(c => c.classList.remove('active'));
  document.getElementById('btn-featured').classList.add('active');
  document.getElementById('country-title').innerText = "Featured Stations";
  document.getElementById('search').value = '';
  
  currentList = [...countryCache["FEATURED"]];
  renderStations(currentList);
}

function loadCountry(code, title) {
  document.querySelectorAll('.country-card').forEach(c => c.classList.remove('active'));
  document.getElementById(`btn-${code}`).classList.add('active');
  document.getElementById('country-title').innerText = `${title} Stations`;
  document.getElementById('search').value = '';

  if (countryCache[code]) {
    currentList = [...countryCache[code]];
    renderStations(currentList);
    return;
  }
  
  showLoadingSkeleton();
  
  fetch(`/api/stations/${code}`)
    .then(r => r.json())
    .then(data => {
      if (data.status === 'ok') {
        countryCache[code] = data.stations;
        
        data.stations.forEach(s => {
          STATIONS[s.key] = { name: s.name, url: s.url, favicon: s.favicon };
        });
        
        currentList = [...data.stations];
        renderStations(currentList);
      }
    })
    .catch(err => {
      console.error(`Error loading stations for ${code}:`, err);
      document.getElementById('stations-grid').innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: var(--danger); padding: 40px;">Failed to load stations. Please try again.</div>';
    });
}

function filterStations() {
  const query = document.getElementById('search').value.toLowerCase().trim();
  if (query === '') {
    renderStations(currentList);
    return;
  }
  
  const filtered = currentList.filter(s => s.name.toLowerCase().includes(query));
  renderStations(filtered);
}

document.addEventListener('DOMContentLoaded', () => {
  renderStations(currentList);
  renderFavorites();
  updateStarIcons();
  pollStatus();
  setInterval(pollStatus, 1500);
});
</script>
</body>
</html>"""

# ── Flask app ────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        cs=COUNTRY_SOURCES,
        rs=radio_stations,
        v=APP_VERSION,
        gs=grouped_stations,
    )


@app.route("/api/stations/<country_code>")
def get_country_stations(country_code):
    stations = _fetch_one(country_code)
    
    country_name = "Country"
    for code, name, flag in COUNTRY_SOURCES:
        if code == country_code:
            country_name = name
            break
            
    global next_station_key
    formatted_stations = []
    for s in stations:
        key = str(next_station_key)
        radio_stations[key] = {"name": f"{country_name}: {s['name']}", "url": s["url"], "favicon": s["favicon"]}
        formatted_stations.append({
            "key": key,
            "name": s["name"],
            "url": s["url"],
            "codec": s["codec"],
            "bitrate": s["bitrate"],
            "tags": s["tags"],
            "favicon": s["favicon"]
        })
        next_station_key += 1
        
    return {"status": "ok", "stations": formatted_stations}


@app.route("/play/<key>")
def play_station(key):
    global current_station_name
    if key not in radio_stations:
        return {"error": "Invalid station"}, 404
    station = radio_stations[key]
    current_station_name = station["name"]
    threading.Thread(target=player.play, args=(station["url"],), daemon=True).start()
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1":
        return {"status": "ok", "station": current_station_name}
    return redirect(url_for("index"))


@app.route("/stop", methods=["POST"])
def stop():
    global current_station_name, sleep_timer, sleep_timer_end_time
    player.stop()
    current_station_name = None
    if sleep_timer:
        sleep_timer.cancel()
        sleep_timer = None
        sleep_timer_end_time = None
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1":
        return {"status": "ok"}
    return redirect(url_for("index"))


@app.route("/volume/<int:level>", methods=["POST"])
def set_volume(level):
    player.set_volume(level)
    return {"status": "ok", "volume": level}


@app.route("/api/sleep/<int:minutes>", methods=["POST"])
def set_sleep_timer(minutes):
    global sleep_timer, sleep_timer_end_time
    if sleep_timer:
        sleep_timer.cancel()
        sleep_timer = None
        sleep_timer_end_time = None
    
    if minutes > 0:
        sleep_timer_end_time = time.time() + (minutes * 60)
        sleep_timer = threading.Timer(minutes * 60, _stop_from_timer)
        sleep_timer.daemon = True
        sleep_timer.start()
        logger.info("Sleep timer scheduled for %d minutes", minutes)
        return {"status": "ok", "remaining": minutes * 60}
    
    logger.info("Sleep timer canceled")
    return {"status": "ok", "remaining": 0}


@app.route("/status")
def get_status():
    global sleep_timer_end_time
    st = player.status()
    remaining_sleep = None
    if sleep_timer_end_time is not None:
        remaining_sleep = max(0, int(sleep_timer_end_time - time.time()))
        if remaining_sleep == 0:
            sleep_timer_end_time = None
            remaining_sleep = None
            
    return {
        "status": "ok",
        "state": st["state"],
        "error": st["error"],
        "url": st["url"],
        "volume": st["volume"],
        "current_station": current_station_name if player.is_playing() else None,
        "now_playing": st.get("now_playing"),
        "sleep_remaining": remaining_sleep,
    }


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
