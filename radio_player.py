"""VLC-based radio player with event tracking."""

import logging
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


try:
    import vlc as loaded_vlc
    vlc = loaded_vlc
except (ImportError, OSError):
    if sys.platform.startswith("linux"):
        _bootstrap_linux()
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

        def status(self) -> dict:
            return {
                "state": self._state,
                "error": self._error,
                "url": self._url,
                "volume": self._volume,
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
