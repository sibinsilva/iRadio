"""VLC-based radio player with event tracking."""

import logging
import os
import sys
import threading
import shutil
import subprocess

logger = logging.getLogger(__name__)

_VLC_ARGS = "--no-video --no-xlib" if sys.platform != "darwin" else "--no-video"

vlc = None
_vlc_error_msg = None


def _bootstrap_linux():
    global vlc, _vlc_error_msg
    if shutil.which("apt-get"):
        logger.info("libvlc not found. Bootstrapping minimal headless packages on Debian/Rpi...")
        try:
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run([
                "sudo", "apt-get", "install", "-y", "--no-install-recommends",
                "libvlc-dev", "vlc-plugin-base"
            ], check=True)
            logger.info("Headless VLC packages installed successfully.")
            import vlc as loaded_vlc
            vlc = loaded_vlc
        except Exception as e:
            _vlc_error_msg = f"Raspberry Pi bootstrap failed: {e}. Please run manually: sudo apt-get install --no-install-recommends libvlc-dev vlc-plugin-base"
            logger.error(_vlc_error_msg)
    else:
        _vlc_error_msg = "VLC library missing. Please install libvlc-dev vlc-plugin-base on your system."
        logger.error(_vlc_error_msg)


def _bootstrap_windows():
    global vlc, _vlc_error_msg
    import zipfile
    import urllib.request
    import io

    dest_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlc_portable")
    os.makedirs(dest_dir, exist_ok=True)
    vlc_subdir = os.path.join(dest_dir, "vlc-3.0.20")

    if not os.path.exists(vlc_subdir):
        url = "https://get.videolan.org/vlc/3.0.20/win64/vlc-3.0.20-win64.zip"
        logger.info("VLC not found. Downloading portable VLC 64-bit from %s...", url)
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                zip_data = resp.read()
            logger.info("Download complete. Extracting to project directory...")
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_ref:
                zip_ref.extractall(dest_dir)
            logger.info("Extraction complete.")
        except Exception as e:
            _vlc_error_msg = f"Windows portable VLC bootstrap failed: {e}. Please install VLC manually."
            logger.error(_vlc_error_msg)
            return

    if os.path.exists(vlc_subdir):
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(vlc_subdir)
            else:
                os.environ["PATH"] = vlc_subdir + os.pathsep + os.environ["PATH"]
            import vlc as loaded_vlc
            vlc = loaded_vlc
            logger.info("Portable VLC loaded successfully.")
        except Exception as e:
            _vlc_error_msg = f"Failed to load portable VLC DLLs: {e}. Please install VLC manually."
            logger.error(_vlc_error_msg)


try:
    import vlc as loaded_vlc
    vlc = loaded_vlc
except (ImportError, OSError):
    if sys.platform.startswith("linux"):
        _bootstrap_linux()
    elif sys.platform == "win32":
        _bootstrap_windows()
    else:
        _vlc_error_msg = "VLC Player not found on this system. Please download/install VLC (64-bit)."


class DummyPlayer:
    """Fallback player when VLC is not installed/loadable."""

    def __init__(self, error_msg):
        self._state = "error"
        self._error = error_msg
        self._url = None
        self._volume = 80

    def play(self, url: str) -> None:
        self._url = url
        self._state = "error"
        logger.error("Cannot play: %s", self._error)

    def stop(self) -> None:
        self._state = "stopped"

    def is_playing(self) -> bool:
        return False

    def set_volume(self, level: int) -> None:
        self._volume = level

    def status(self) -> dict:
        return {
            "state": self._state,
            "error": self._error,
            "url": self._url,
            "volume": self._volume,
            "now_playing": None,
        }


if vlc is not None:
    class RadioPlayer:
        """Thread-safe internet radio player backed by libVLC."""

        __slots__ = ("_instance", "_player", "_lock", "_state", "_error", "_url", "_volume")

        def __init__(self):
            self._instance = None
            self._player = None
            self._lock = threading.RLock()
            self._state = None
            self._error = None
            self._url = None
            self._volume = 80

        # ── VLC event callbacks ──────────────────────────────────────────

        def _attach_events(self):
            em = self._player.event_manager()
            for evt, cb in (
                (vlc.EventType.MediaPlayerPlaying, self._on_playing),
                (vlc.EventType.MediaPlayerEncounteredError, self._on_error),
                (vlc.EventType.MediaPlayerStopped, self._on_stopped),
                (vlc.EventType.MediaPlayerBuffering, self._on_buffering),
                (vlc.EventType.MediaPlayerEndReached, self._on_end),
            ):
                em.event_attach(evt, cb)

        def _detach_events(self):
            if self._player:
                em = self._player.event_manager()
                for evt in (
                    vlc.EventType.MediaPlayerPlaying,
                    vlc.EventType.MediaPlayerEncounteredError,
                    vlc.EventType.MediaPlayerStopped,
                    vlc.EventType.MediaPlayerBuffering,
                    vlc.EventType.MediaPlayerEndReached,
                ):
                    try:
                        em.event_detach(evt)
                    except Exception:
                        pass

        def _on_playing(self, _):
            self._state = "playing"

        def _on_error(self, _):
            self._state = "error"
            self._error = "VLC playback error"

        def _on_stopped(self, _):
            self._state = "stopped"

        def _on_buffering(self, _):
            self._state = "buffering"

        def _on_end(self, _):
            self._state = "ended"

        # ── Public API ───────────────────────────────────────────────────

        def play(self, url: str) -> None:
            with self._lock:
                self._release_player()
                self._error = None
                self._state = "starting"
                self._url = url
                logger.info("Play url=%s", url)
                if not self._instance:
                    try:
                        self._instance = vlc.Instance(_VLC_ARGS)
                    except Exception as exc:
                        self._error = f"VLC init failed: {exc}"
                        self._state = "error"
                        logger.exception("VLC init failed")
                        return
                self._player = self._instance.media_player_new()
                self._attach_events()
                self._player.set_media(self._instance.media_new(url))
                self._player.audio_set_volume(self._volume)
                self._player.play()

        def stop(self) -> None:
            with self._lock:
                self._release_player()

        def is_playing(self) -> bool:
            return bool(self._player and self._player.is_playing())

        def set_volume(self, level: int) -> None:
            with self._lock:
                self._volume = max(0, min(100, level))
                if self._player:
                    self._player.audio_set_volume(self._volume)

        def get_now_playing(self) -> str | None:
            with self._lock:
                if not self._player:
                    return None
                media = self._player.get_media()
                if not media:
                    return None
                try:
                    now_playing = media.get_meta(vlc.Meta.NowPlaying)
                    if now_playing:
                        return now_playing.strip()
                    title = media.get_meta(vlc.Meta.Title)
                    artist = media.get_meta(vlc.Meta.Artist)
                    if title and artist:
                        return f"{artist.strip()} - {title.strip()}"
                    elif title:
                        return title.strip()
                except Exception:
                    pass
                return None

        def status(self) -> dict:
            return {
                "state": self._state,
                "error": self._error,
                "url": self._url,
                "volume": self._volume,
                "now_playing": self.get_now_playing(),
            }

        # ── Internal ─────────────────────────────────────────────────────

        def _release_player(self):
            if self._player:
                self._detach_events()
                self._player.stop()
                self._player = None
            self._state = "stopped"

        def _release(self):
            self._release_player()
            if self._instance:
                self._instance.release()
                self._instance = None
else:
    class RadioPlayer(DummyPlayer):
        """Dynamic fallback class when VLC is not available."""

        def __init__(self):
            super().__init__(_vlc_error_msg or "VLC Player not found on this system.")
