"""Playback backends.

- MPVBackend: drives mpv over an IPC socket (JSON). Supports true pause/resume,
  instant volume changes, playback position tracking, end-of-track detection,
  and exposes the real audio codec/format being decoded.
- FFplayBackend: fallback using ffplay (no IPC) -- pause = kill & replay,
  volume applies to the next track, no progress bar and no codec info.

Common interface (duck typing):
    start(volume)        -> launch the background process
    play(url)            -> play a URL (replacing the current track)
    toggle_pause()       -> flip pause/resume, returns bool is-paused (None if unsupported)
    stop()               -> stop and go idle
    set_volume(v)        -> set volume 0-100
    seek(seconds)        -> relative seek (None if unsupported)
    quit()               -> shut the backend down

Readable attributes (updated in the background):
    supports_live  : bool   -> has runtime control (pause/seek/volume)
    paused         : bool
    time_pos       : float | None  (seconds)
    duration       : float | None
    track_ended    : bool   -> True when the track ended on its own (app reads & resets)
    audio_*        : info about the audio stream actually playing
    name           : str    -> backend name
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path


class PlayerBackend:
    name = "base"
    supports_live = False

    def start(self, volume: int) -> None: ...
    def play(self, url: str) -> None: ...
    def toggle_pause(self) -> bool | None: ...
    def stop(self) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def seek(self, seconds: float) -> bool | None: return None
    def quit(self) -> None: ...

    paused: bool = False
    time_pos: float | None = None
    duration: float | None = None
    track_ended: bool = False

    # Real audio stream info (None if the backend does not support it)
    audio_codec: str | None = None
    audio_samplerate: int | None = None
    audio_channels: int | None = None
    audio_format: str | None = None   # e.g. s16, s32, float
    audio_bitrate: int | None = None  # bits/s

    def describe_audio(self) -> str:
        """Short one-line description of the audio stream being played."""
        if not self.audio_codec:
            return ""
        parts = [self.audio_codec.upper()]
        if self.audio_samplerate:
            parts.append(f"{self.audio_samplerate / 1000:g}kHz")
        if self.audio_format:
            # bit depth from the format name: s16 -> 16bit, s32 -> 32bit
            digits = "".join(c for c in self.audio_format if c.isdigit())
            if digits:
                parts.append(f"{digits}bit")
        if self.audio_channels:
            ch = {1: "mono", 2: "stereo"}.get(self.audio_channels, f"{self.audio_channels}ch")
            parts.append(ch)
        if self.audio_bitrate:
            parts.append(f"{round(self.audio_bitrate / 1000)}kbps")
        return " · ".join(parts)


# ----------------------------------------------------------------------------


class MPVBackend(PlayerBackend):
    name = "mpv"
    supports_live = True

    # Quality tuning and glitch/underrun avoidance for network streaming.
    # Placed before extra_args so the user can still override (mpv prefers later flags).
    QUALITY_ARGS = [
        # --- Network buffering: avoid underruns that cause crackling/stutter ---
        "--cache=yes",
        "--cache-secs=60",            # buffer up to 60s ahead
        "--demuxer-max-bytes=64MiB",
        "--demuxer-max-back-bytes=32MiB",
        "--demuxer-readahead-secs=30",
        "--network-timeout=30",
        # --- High quality audio output ---
        "--audio-buffer=0.2",         # slightly larger device buffer for smoothness
        "--gapless-audio=yes",        # seamless track changes, no clicks
        "--audio-channels=auto-safe", # keep the original channel layout when supported
        "--volume-max=100",
        "--audio-pitch-correction=yes",
    ]

    def __init__(self, mpv_path: str = "mpv", extra_args: list | None = None,
                 socket_dir: str | None = None):
        self.mpv_path = mpv_path
        self.extra_args = extra_args or []
        self.socket_dir = socket_dir
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self._sock_path = ""
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._running = False
        self._volume = 100
        self._stopped_by_user = False
        self._has_track = False

        self.paused = False
        self.time_pos = None
        self.duration = None
        self.track_ended = False
        self.audio_codec = None
        self.audio_samplerate = None
        self.audio_channels = None
        self.audio_format = None
        self.audio_bitrate = None

    # ---- lifecycle ----

    def start(self, volume: int) -> None:
        self._volume = volume
        base = self.socket_dir or os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        self._sock_path = str(Path(base) / f"navidrome-mpv-{os.getpid()}.sock")
        if os.path.exists(self._sock_path):
            os.unlink(self._sock_path)

        cmd = [
            self.mpv_path,
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--force-window=no",
            f"--volume={volume}",
            f"--input-ipc-server={self._sock_path}",
            *self.QUALITY_ARGS,
            *self.extra_args,
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Wait for mpv to create the socket
        for _ in range(100):
            if os.path.exists(self._sock_path):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self._sock_path)
                    self.sock = s
                    break
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError("mpv exited immediately on startup")
            time.sleep(0.05)
        else:
            raise RuntimeError("Could not connect to the mpv IPC socket")

        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # Observe the properties we need
        for i, prop in enumerate((
            "time-pos", "duration", "pause", "idle-active",
            "audio-codec-name", "audio-params", "audio-bitrate",
        ), start=1):
            self._send({"command": ["observe_property", i, prop]})

    # ---- IPC ----

    def _send(self, obj: dict) -> None:
        if not self.sock:
            return
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.sock.sendall(data)
            except OSError:
                pass

    def _read_loop(self) -> None:
        buf = b""
        while self._running and self.sock:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._handle(line)

    def _handle(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        if msg.get("event") != "property-change":
            return
        name, data = msg.get("name"), msg.get("data")
        if name == "time-pos":
            self.time_pos = data
        elif name == "duration":
            self.duration = data
        elif name == "pause":
            self.paused = bool(data)
        elif name == "idle-active":
            # mpv going idle means the track just finished
            if data and self._has_track and not self._stopped_by_user:
                self.track_ended = True
                self._has_track = False
        elif name == "audio-codec-name":
            self.audio_codec = data
        elif name == "audio-bitrate":
            self.audio_bitrate = int(data) if data else None
        elif name == "audio-params" and isinstance(data, dict):
            self.audio_samplerate = data.get("samplerate")
            self.audio_channels = data.get("channel-count")
            self.audio_format = data.get("format")

    # ---- control ----

    def play(self, url: str) -> None:
        self._stopped_by_user = False
        self._has_track = True
        self.track_ended = False
        self.time_pos = 0.0
        self.duration = None
        self.paused = False
        self.audio_codec = None
        self.audio_samplerate = None
        self.audio_channels = None
        self.audio_format = None
        self.audio_bitrate = None
        self._send({"command": ["loadfile", url, "replace"]})
        self._send({"command": ["set_property", "pause", False]})

    def toggle_pause(self) -> bool | None:
        self._send({"command": ["cycle", "pause"]})
        self.paused = not self.paused
        return self.paused

    def stop(self) -> None:
        self._stopped_by_user = True
        self._has_track = False
        self.time_pos = None
        self.duration = None
        self.paused = False
        self._send({"command": ["stop"]})

    def set_volume(self, volume: int) -> None:
        self._volume = volume
        self._send({"command": ["set_property", "volume", volume]})

    def seek(self, seconds: float) -> bool | None:
        self._send({"command": ["seek", seconds, "relative"]})
        return True

    @property
    def is_active(self) -> bool:
        return self._has_track

    def quit(self) -> None:
        self._running = False
        self._send({"command": ["quit"]})
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._sock_path and os.path.exists(self._sock_path):
            try:
                os.unlink(self._sock_path)
            except OSError:
                pass


# ----------------------------------------------------------------------------


class FFplayBackend(PlayerBackend):
    name = "ffplay"
    supports_live = False

    def __init__(self, ffplay_path: str = "ffplay", extra_args: list | None = None):
        self.ffplay_path = ffplay_path
        self.extra_args = extra_args or []
        self.proc: subprocess.Popen | None = None
        self._volume = 100
        self._url: str | None = None
        self.paused = False
        self.time_pos = None
        self.duration = None
        self.track_ended = False

    def start(self, volume: int) -> None:
        self._volume = volume

    def _spawn(self, url: str) -> None:
        self._kill()
        cmd = [
            self.ffplay_path, "-nodisp", "-autoexit", "-loglevel", "quiet",
            "-volume", str(self._volume), *self.extra_args, url,
        ]
        self.proc = subprocess.Popen(cmd)

    def _kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def play(self, url: str) -> None:
        self._url = url
        self.paused = False
        self.track_ended = False
        self._spawn(url)

    def toggle_pause(self) -> bool | None:
        # ffplay cannot pause -> emulate by kill / replay from the start
        if self.proc and self.proc.poll() is None:
            self._kill()
            self.paused = True
        elif self._url:
            self._spawn(self._url)
            self.paused = False
        return self.paused

    def stop(self) -> None:
        self._kill()
        self.paused = False
        self._url = None

    def set_volume(self, volume: int) -> None:
        self._volume = volume  # applies to the next track

    @property
    def is_active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def poll_ended(self) -> None:
        """Called periodically by the app to detect ffplay finishing (track end)."""
        if self._url and not self.paused and self.proc and self.proc.poll() is not None:
            self.track_ended = True
            self.proc = None

    def quit(self) -> None:
        self._kill()


# ----------------------------------------------------------------------------


def make_backend(prefer: str, mpv_path: str, ffplay_path: str, extra_args: list,
                 volume: int = 100, socket_dir: str | None = None) -> PlayerBackend:
    """Create a backend by preference, falling back to ffplay if mpv fails."""
    import shutil

    want_mpv = prefer in ("mpv", "auto")
    if want_mpv and shutil.which(mpv_path):
        backend = MPVBackend(mpv_path, extra_args, socket_dir)
        try:
            backend.start(volume)
            return backend
        except Exception:
            try:
                backend.quit()
            except Exception:
                pass
    fb = FFplayBackend(ffplay_path, extra_args)
    fb.start(volume)
    return fb
