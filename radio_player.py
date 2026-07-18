"""VLC-based radio player with event tracking."""

import logging
import sys
import threading

import vlc

logger = logging.getLogger(__name__)

_VLC_ARGS = "--no-video --no-xlib" if sys.platform != "darwin" else "--no-video"


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
