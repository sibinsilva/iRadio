"""VLC-based radio player with event tracking."""

import logging
import sys
import threading

import vlc

logger = logging.getLogger(__name__)

_VLC_ARGS = "--no-xlib" if sys.platform != "darwin" else ""


class RadioPlayer:
    """Thread-safe internet radio player backed by libVLC."""

    __slots__ = ("_instance", "_player", "_lock", "_state", "_error", "_url")

    def __init__(self):
        self._instance = None
        self._player = None
        self._lock = threading.RLock()
        self._state = None
        self._error = None
        self._url = None

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

    def _on_playing(self, _):
        self._state = "playing"
        logger.info("VLC playing url=%s", self._url)

    def _on_error(self, _):
        self._state = "error"
        self._error = "VLC playback error"
        logger.error("VLC error url=%s", self._url)

    def _on_stopped(self, _):
        self._state = "stopped"

    def _on_buffering(self, _):
        self._state = "buffering"

    def _on_end(self, _):
        self._state = "ended"

    # ── Public API ───────────────────────────────────────────────────

    def play(self, url: str) -> None:
        with self._lock:
            self._release()
            self._error = None
            self._state = "starting"
            self._url = url
            logger.info("Play url=%s", url)
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
            self._player.audio_set_volume(100)
            self._player.play()

    def stop(self) -> None:
        with self._lock:
            self._release()

    def is_playing(self) -> bool:
        return bool(self._player and self._player.is_playing())

    def status(self) -> dict:
        return {"state": self._state, "error": self._error, "url": self._url}

    # ── Internal ─────────────────────────────────────────────────────

    def _release(self):
        if self._player:
            self._player.stop()
        if self._instance:
            self._instance.release()
        self._player = self._instance = None
        self._state = "stopped"
