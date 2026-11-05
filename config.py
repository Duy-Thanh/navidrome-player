"""Configuration for the Navidrome TUI Player.

Precedence (highest to lowest):
  1. Environment variables (NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS, ...)
  2. TOML config file
  3. Built-in defaults

The config file is looked up in this order:
  - Path in the NAVIDROME_CONFIG environment variable
  - ./config.toml  (current working directory)
  - $XDG_CONFIG_HOME/navidrome-player/config.toml
  - ~/.config/navidrome-player/config.toml
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    """Raised for invalid or incomplete configuration."""


DEFAULTS: dict = {
    "server": {
        "url": "http://localhost:4533",
        "username": "",
        "password": "",
        "timeout": 10,
    },
    "streaming": {
        # "raw" + max_bitrate 0 = Navidrome sends the original file, no transcoding.
        # Use format = "" to let the server decide, or "mp3"/"opus"... to force transcode.
        "format": "raw",
        "max_bitrate": 0,       # 0 = unlimited (original). e.g. 320 to cap at 320 kbps.
    },
    "player": {
        "backend": "auto",      # auto | mpv | ffplay
        "mpv_path": "mpv",
        "ffplay_path": "ffplay",
        "volume": 100,          # 0-100
        "extra_args": [],       # extra player flags
    },
    "ui": {
        "page_size": 50,        # number of tracks loaded per request
        "default_source": "random",  # random | newest | frequent | recent | starred
        "accent": "cyan",
        "success": "green",
        "error": "red",
        "theme_dark": True,
        "show_clock": True,
    },
}


def _config_candidates() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("NAVIDROME_CONFIG")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(Path.cwd() / "config.toml")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    paths.append(base / "navidrome-player" / "config.toml")
    return paths


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _load_toml() -> tuple[dict, Path | None]:
    for path in _config_candidates():
        if path.is_file():
            try:
                with path.open("rb") as fh:
                    return tomllib.load(fh), path
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"Config file '{path}' has a syntax error: {exc}") from exc
    return {}, None


def _apply_env(data: dict) -> dict:
    """Override values from environment variables (only when they are set)."""
    env_map = {
        ("server", "url"): "NAVIDROME_URL",
        ("server", "username"): "NAVIDROME_USER",
        ("server", "password"): "NAVIDROME_PASS",
        ("player", "ffplay_path"): "NAVIDROME_FFPLAY",
        ("player", "mpv_path"): "NAVIDROME_MPV",
        ("player", "backend"): "NAVIDROME_BACKEND",
    }
    for (section, key), env_name in env_map.items():
        val = os.environ.get(env_name)
        if val:
            data.setdefault(section, {})[key] = val

    vol = os.environ.get("NAVIDROME_VOLUME")
    if vol:
        try:
            data.setdefault("player", {})["volume"] = int(vol)
        except ValueError:
            pass
    return data


@dataclass
class ServerConfig:
    url: str
    username: str
    password: str
    timeout: int = 10


@dataclass
class StreamingConfig:
    format: str = "raw"
    max_bitrate: int = 0


@dataclass
class PlayerConfig:
    backend: str = "auto"
    mpv_path: str = "mpv"
    ffplay_path: str = "ffplay"
    volume: int = 100
    extra_args: list = field(default_factory=list)


@dataclass
class UIConfig:
    page_size: int = 50
    default_source: str = "random"
    accent: str = "cyan"
    success: str = "green"
    error: str = "red"
    theme_dark: bool = True
    show_clock: bool = True


@dataclass
class Config:
    server: ServerConfig
    streaming: StreamingConfig
    player: PlayerConfig
    ui: UIConfig
    source_path: Path | None = None

    @classmethod
    def load(cls) -> "Config":
        file_data, path = _load_toml()
        merged = _deep_merge(DEFAULTS, file_data)
        merged = _apply_env(merged)

        srv = merged["server"]
        url = str(srv.get("url", "")).rstrip("/")
        username = str(srv.get("username", ""))
        password = str(srv.get("password", ""))

        missing = [n for n, v in (("url", url), ("username", username),
                                  ("password", password)) if not v]
        if missing:
            raise ConfigError(
                "Missing configuration: " + ", ".join(missing) + ".\n"
                "Create a config.toml (see config.example.toml) or set the "
                "NAVIDROME_URL / NAVIDROME_USER / NAVIDROME_PASS environment variables."
            )

        volume = max(0, min(100, int(merged["player"].get("volume", 100))))

        return cls(
            server=ServerConfig(url=url, username=username, password=password,
                                timeout=int(srv.get("timeout", 10))),
            streaming=StreamingConfig(
                format=str(merged["streaming"].get("format", "raw")),
                max_bitrate=int(merged["streaming"].get("max_bitrate", 0)),
            ),
            player=PlayerConfig(
                backend=str(merged["player"].get("backend", "auto")),
                mpv_path=str(merged["player"].get("mpv_path", "mpv")),
                ffplay_path=str(merged["player"].get("ffplay_path", "ffplay")),
                volume=volume,
                extra_args=list(merged["player"].get("extra_args", [])),
            ),
            ui=UIConfig(
                page_size=int(merged["ui"].get("page_size", 50)),
                default_source=str(merged["ui"].get("default_source", "random")),
                accent=str(merged["ui"].get("accent", "cyan")),
                success=str(merged["ui"].get("success", "green")),
                error=str(merged["ui"].get("error", "red")),
                theme_dark=bool(merged["ui"].get("theme_dark", True)),
                show_clock=bool(merged["ui"].get("show_clock", True)),
            ),
            source_path=path,
        )
