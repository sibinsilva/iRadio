# iRadio

A lightweight web-based internet radio player for your desktop. iRadio fetches live stations from the [Radio Browser API](https://www.radio-browser.info/) and streams them through VLC, presenting a clean dark-themed interface in your browser.

![iRadio Player UI](https://img.shields.io/badge/UI-Web%20Browser-38bdf8?style=flat-square) ![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python) ![Flask](https://img.shields.io/badge/Flask-web%20server-000000?style=flat-square&logo=flask) ![VLC](https://img.shields.io/badge/VLC-libvlc-FF8800?style=flat-square&logo=vlc-media-player)

---

## Features

- **Multi-country station catalog** — up to 50 stations each from India, Ireland, UK, Canada, USA, and Dubai
- **Automatic station discovery** — stations are fetched in parallel from the Radio Browser API on startup
- **Codec filtering** — only MP3, AAC, AAC+, OGG, and Opus streams are included (no HLS)
- **Fallback stations** — Radio Paradise, SomaFM Groove Salad, and KEXP are used when the API is unavailable
- **Dark-themed web UI** — collapsible country sections, card-based station grid, real-time playback status
- **VLC-powered playback** — reliable streaming backed by libVLC with event-driven state tracking
- **Health check endpoint** — `/health` returns JSON with app version and station count

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | |
| VLC / libVLC | Must be installed separately (see below) |
| `flask` | Python package |
| `python-vlc` | Python bindings for libVLC |

### Install VLC

**Ubuntu / Debian**
```bash
sudo apt-get install vlc libvlc-dev
```

**macOS (Homebrew)**
```bash
brew install --cask vlc
```

**Windows**  
Download and install VLC from <https://www.videolan.org/vlc/>.

---

## Installation

```bash
# Clone the repository
git clone https://github.com/sibinsilva/iRadio.git
cd iRadio

# Install Python dependencies
pip install flask python-vlc
```

---

## Usage

```bash
python iRadio.py
```

The app will:
1. Fetch stations from the Radio Browser API (runs in parallel — takes a few seconds)
2. Start a Flask web server at `http://127.0.0.1:5000/`
3. Automatically open the interface in your default web browser

### Web Interface

- Click a **country section** to expand it and see available stations
- Click **Play** on any station card to start streaming
- Click **Stop** at the top to stop playback
- The "Now Playing" bar shows the current station name and VLC state (`buffering`, `playing`, `error`, …)

---

## Project Structure

```
iRadio/
├── iRadio.py          # Entry point — calls web_app.run()
├── radio_player.py    # Thread-safe VLC-backed RadioPlayer class
└── web_app.py         # Flask app: station fetching, routing, HTML template
```

| File | Responsibility |
|---|---|
| `iRadio.py` | Thin entry point |
| `radio_player.py` | Wraps libVLC; exposes `play(url)`, `stop()`, `is_playing()`, `status()` |
| `web_app.py` | Fetches & groups stations, serves the web UI, handles all HTTP routes |

---

## API Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Main web interface |
| `GET` | `/play/<key>` | Start playing the station with the given key |
| `POST` | `/stop` | Stop the currently playing station |
| `GET` | `/health` | JSON health check: `{ status, stations, version }` |

---

## Configuration

All configuration lives in `web_app.py` as module-level constants:

| Constant | Default | Description |
|---|---|---|
| `STATIONS_PER_COUNTRY` | `50` | Maximum stations fetched per country |
| `ALLOWED_CODECS` | `mp3, aac, aac+, ogg, opus` | Stream codecs accepted |
| `COUNTRY_SOURCES` | IN, IE, GB, CA, US, AE | Countries and their ISO codes |
| `FALLBACK_STATIONS` | Radio Paradise, SomaFM, KEXP | Used when the API is unreachable |
| `APP_VERSION` | `2026-03-04-5` | Displayed in the UI and health endpoint |

### Environment Variables

| Variable | Description |
|---|---|
| `IRADIO_OPEN_IE=1` | Open the browser using Internet Explorer (Windows only) |

---

## License

This project does not currently include a license file. All rights reserved by the author.
