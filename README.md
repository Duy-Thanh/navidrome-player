# Navidrome TUI Player

A terminal music player for Navidrome/Subsonic (Textual + mpv/ffplay).

## Install

```bash
pip install -r requirements.txt   # textual, requests
# Install mpv for full features (runtime pause/seek/instant volume):
#   sudo pacman -S mpv      # or the apt/dnf equivalent
# Without mpv, the app falls back to ffplay (FFmpeg) with limited features.
cp config.example.toml config.toml   # then edit your server details
python player.py
```

You can use environment variables instead of a config file:
`NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASS`, `NAVIDROME_MPV`,
`NAVIDROME_FFPLAY`, `NAVIDROME_BACKEND`, `NAVIDROME_VOLUME`, `NAVIDROME_CONFIG`.

## Keybindings

| Key | Action |
|-----|--------|
| `Enter` | Play selected track / run search |
| `Space` | Play / Pause |
| `s` | Stop |
| `n` / `p` | Next / previous track |
| `←` / `→` | Seek -10s / +10s (mpv) |
| `r` | Reload list |
| `f` | Focus the search box |
| `t` | Star / unstar ⭐ |
| `+` / `-` | Volume up / down |
| `q` | Quit |

## Features
- No hardcoding: configure via `config.toml` or environment variables.
- mpv backend over an IPC socket: **true pause/resume, seeking, instant volume**,
  a time progress bar, **auto-advance when a track ends**, and a live readout of the
  real codec/sample rate/bit depth being decoded. Falls back to ffplay when mpv is missing.
- Multiple sources: random, newest, most played, recently played, starred.
- Track/artist search.
- Next/previous, star, visual volume control.
- Bit-perfect streaming from Navidrome (`format=raw`, `max_bitrate=0`).
- List loading and search run in the background (the UI never freezes).

## Files
- `player.py` — Textual UI and app logic.
- `backends.py` — mpv (IPC) and ffplay playback backends.
- `subsonic.py` — Subsonic/Navidrome API client.
- `config.py` — configuration loader (env vars → TOML → defaults).
