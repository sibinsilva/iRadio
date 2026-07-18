# iRadio 📻

A gorgeous, zero-configuration, glassmorphism web-based internet radio player. iRadio streams 1,000+ live stations from the [Radio Browser API](https://www.radio-browser.info/) and plays them through libVLC, presenting a stunning desktop experience.

![iRadio UI](https://img.shields.io/badge/UI-Glassmorphism%20SPA-06b6d4?style=flat-square) ![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat-square&logo=python) ![Flask](https://img.shields.io/badge/Flask-Web%20Server-000000?style=flat-square&logo=flask) ![VLC](https://img.shields.io/badge/VLC-Headless%20libvlc-FF8800?style=flat-square&logo=vlc-media-player)

---

## Key Features

*   **Modern Glassmorphism SPA**: High-fidelity dark mode with neon accents, Outfit typography, glowing states, and a smooth bottom controller complete with an equalizing wave animation.
*   **Station Logos & Color Fallbacks**: Card listings feature lazy-loaded station icons. If a logo is missing or fails to load, it automatically creates a beautiful, unique CSS gradient avatar using the station's initials.
*   **Zero-Configuration VLC Bootstrapping**:
    *   **Windows**: If VLC isn't installed system-wide, the player automatically downloads, extracts, and runs a portable headless VLC build in a local directory (`vlc_portable/`).
    *   **Raspberry Pi / Linux**: Headless environments automatically run `apt-get` packages installation (`libvlc-dev`, `vlc-plugin-base`).
    *   **DummyPlayer Fallback**: If VLC completely fails to load, the app runs in a degraded mode, preventing startup crashes and showing setup instructions in the UI.
*   **Unlimited Dynamic Streams**: Fetches 1,000+ stations sorted by popularity (votes). It instantly renders the Top 10 popular stations for each country, keeping the rest expandable under a "Show More (+X)" shelf.
*   **Real-time Search & Favorites**: Instantly filters station cards by name in real-time. Star your favorite stations to pin them to the top of the dashboard (saved in browser `localStorage`).
*   **One-Click Package Command**: Fully packaged using Python Wheel. Build and install it once, then launch it from anywhere simply by running `iradio`.

---

## Installation & Setup

iRadio can be installed natively as a Python package, which works identically on both **Windows** and **Linux/Raspberry Pi**.

### 1. Build and Install the Wheel
```bash
# Clone the repository
git clone https://github.com/sibinsilva/iRadio.git
cd iRadio

# Build the package
pip install build
python -m build

# Install the wheel locally
pip install dist/iradio-1.0.0-py3-none-any.whl
```

### 2. Run iRadio
You can now start the player from any terminal or folder:
```bash
iradio
```
This command starts the Flask server and opens `http://127.0.0.1:5000` in your default web browser automatically.

---

## Dynamic VLC Bootstrapping Internals

The player manages `libvlc` dependencies dynamically upon launching:

1.  **System Check**: Python checks if `vlc` can be loaded globally. If yes, it runs immediately.
2.  **Windows Portable Fallback**: If missing on Windows, it downloads the official 64-bit portable VLC archive, extracts it to `vlc_portable/`, and registers the DLL search path using `os.add_dll_directory` at runtime.
3.  **Linux/Debian Fallback**: If missing on a Debian/Raspberry Pi environment, it invokes a headless bootstrap subprocess:
    ```bash
    sudo apt-get update && sudo apt-get install -y --no-install-recommends libvlc-dev vlc-plugin-base
    ```
4.  **Degraded Mode**: If all setups fail, the app switches to `DummyPlayer`, logging errors to the console and rendering the setup warnings in the web browser controller.

---

## Project Structure

```
iRadio/
├── pyproject.toml     # Packaging and entrypoint console_scripts configuration
├── radio_player.py    # VLC player wrapper with events, RLock threads, and DummyPlayer
├── web_app.py         # Flask routing, Radio Browser API query logic, and SPA HTML/CSS/JS
└── README.md          # Project documentation
```

---

## API & Client Integration

| Route | Method | Description |
|:---|:---|:---|
| `/` | `GET` | Renders the main Single Page Application dashboard |
| `/api/stations/<country_code>` | `GET` | Fetches, deduplicates, and caches country stations dynamically |
| `/play/<key>` | `GET` | Starts background thread streaming of a station stream |
| `/stop` | `POST` | Stops background player streaming |
| `/volume/<level>` | `POST` | Updates the player volume (0 - 100) |
| `/status` | `GET` | Returns player status JSON (state, url, current volume, errors) |
| `/health` | `GET` | Basic check endpoint for container environments |

---

## License

This project is licensed under the MIT License. Feel free to use, modify, and distribute it!
