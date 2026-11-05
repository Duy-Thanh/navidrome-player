"""Navidrome TUI Player -- a terminal music player for Navidrome/Subsonic.

Run:  python player.py
Playback backend: mpv (IPC socket, runtime control) with an ffplay fallback.
Configuration: see config.example.toml or the environment variables (see config.py).
"""

from __future__ import annotations

import sys

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListItem, ListView, ProgressBar, Select, Static,
)

from backends import FFplayBackend, make_backend
from config import Config, ConfigError
from subsonic import SubsonicClient

SOURCES = [
    ("🎲 Random", "random"),
    ("🆕 Newest", "newest"),
    ("🔥 Most played", "frequent"),
    ("🕒 Recently played", "recent"),
    ("⭐ Starred", "starred"),
]


def fmt_duration(seconds: float | int | None) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class TrackItem(ListItem):
    """A ListItem holding track metadata."""

    def __init__(self, song: dict):
        super().__init__()
        self.song = song
        self.track_id = song.get("id", "")
        self.title = song.get("title", "Unknown")
        self.artist = song.get("artist", "Unknown")
        self.duration = song.get("duration")
        self.starred = bool(song.get("starred"))

    def compose(self) -> ComposeResult:
        star = "⭐ " if self.starred else ""
        dur = fmt_duration(self.duration)
        yield Label(f"{star}🎵 {self.title}  [dim]— {self.artist}[/dim]  [dim]({dur})[/dim]")


class NavidromePlayer(App):
    TITLE = "Navidrome TUI Player"
    CSS = """
    #sidebar {
        width: 62%;
        height: 100%;
        border-right: heavy $accent;
        padding: 1;
    }
    #search { margin-bottom: 1; }
    #source_select { margin-bottom: 1; }
    #track_list { height: 1fr; }
    /* Keep the selected row highlighted even when the list is not focused. */
    #track_list > ListItem.-highlight {
        background: $accent 40%;
        color: $text;
        text-style: bold;
    }
    #track_list:focus > ListItem.-highlight {
        background: $accent;
        color: $text;
    }
    #player_panel {
        width: 38%;
        height: 100%;
        padding: 2;
    }
    #track_info {
        content-align: center middle;
        height: 7;
        border: panel $success;
        margin-bottom: 1;
        text-style: bold;
    }
    #progress_row {
        height: 1;
        margin-bottom: 1;
    }
    #time_pos, #time_dur { width: 6; color: $text-muted; }
    #progress { width: 1fr; }
    #audio_info {
        height: 1;
        content-align: center middle;
        color: $success;
        margin-bottom: 1;
    }
    #status_bar {
        height: 3;
        color: $text-muted;
        content-align: center middle;
        margin-bottom: 1;
    }
    #controls { height: auto; align: center top; }
    #controls Button { margin: 0 1 1 1; width: 1fr; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "toggle_pause", "Play/Pause"),
        ("s", "stop", "Stop"),
        ("r", "reload", "Reload"),
        ("f", "focus_search", "Search"),
        ("t", "toggle_star", "Star ⭐"),
        ("n", "next", "Next"),
        ("p", "prev", "Previous"),
        ("right", "seek_fwd", "Seek +10s"),
        ("left", "seek_back", "Seek -10s"),
        ("plus", "volume_up", "Vol +"),
        ("minus", "volume_down", "Vol -"),
    ]

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.client = SubsonicClient(
            config.server.url, config.server.username,
            config.server.password, config.server.timeout,
            stream_format=config.streaming.format,
            max_bitrate=config.streaming.max_bitrate,
        )
        self.backend = make_backend(
            prefer=config.player.backend,
            mpv_path=config.player.mpv_path,
            ffplay_path=config.player.ffplay_path,
            extra_args=config.player.extra_args,
            volume=config.player.volume,
        )
        self.current_item: TrackItem | None = None
        self.volume = config.player.volume
        self.dark = config.ui.theme_dark

    def compose(self) -> ComposeResult:
        yield Header(show_clock=self.config.ui.show_clock)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Input(placeholder="🔎 Search tracks / artists — press Enter", id="search")
                yield Select(SOURCES, value=self._valid_source(), id="source_select",
                             allow_blank=False)
                yield ListView(id="track_list")
            with Vertical(id="player_panel"):
                yield Static("No track selected", id="track_info")
                with Horizontal(id="progress_row"):
                    yield Label("0:00", id="time_pos")
                    yield ProgressBar(total=100, show_eta=False, show_percentage=False,
                                      id="progress")
                    yield Label("--:--", id="time_dur")
                yield Static("", id="audio_info")
                yield Static(self._status_text(), id="status_bar")
                with Vertical(id="controls"):
                    yield Button("⏮  Previous", id="btn_prev")
                    yield Button("▶ / ⏸  Play / Pause", id="btn_toggle", variant="primary")
                    yield Button("⏭  Next", id="btn_next")
                    yield Button("⏹  Stop", id="btn_stop", variant="error")
                    yield Button("⭐ Star", id="btn_star")
                    yield Button("🔄 Reload", id="btn_reload")
        yield Footer()

    def _valid_source(self) -> str:
        valid = {v for _, v in SOURCES}
        return self.config.ui.default_source if self.config.ui.default_source in valid else "random"

    def _status_text(self) -> str:
        bars = "█" * (self.volume // 10) + "░" * (10 - self.volume // 10)
        live = "runtime" if self.backend.supports_live else "limited"
        return f"🔊 [bold]{bars}[/bold] {self.volume}%  ·  backend: [b]{self.backend.name}[/b] ({live})"

    def on_mount(self) -> None:
        self.theme = "textual-dark" if self.dark else "textual-light"
        if not self.backend.supports_live:
            self.notify("Using ffplay (mpv not found) — pause/seek/volume are limited. "
                        "Install mpv for full features.", severity="warning", timeout=6)
        self.set_interval(0.5, self._tick)
        self.load_source(self._valid_source())

    # ---------------- Progress updates / auto-advance ----------------

    def _tick(self) -> None:
        # ffplay must be polled to know the track has ended
        if isinstance(self.backend, FFplayBackend):
            self.backend.poll_ended()

        if self.backend.track_ended:
            self.backend.track_ended = False
            self.action_next()
            return

        info = self.backend.describe_audio()
        self.query_one("#audio_info", Static).update(f"🎧 {info}" if info else "")

        pos, dur = self.backend.time_pos, self.backend.duration
        bar = self.query_one("#progress", ProgressBar)
        self.query_one("#time_pos", Label).update(fmt_duration(pos))
        self.query_one("#time_dur", Label).update(fmt_duration(dur))
        if dur and dur > 0 and pos is not None:
            bar.update(total=dur, progress=min(pos, dur))
        else:
            bar.update(total=100, progress=0)

    # ---------------- Loading track lists ----------------

    @work(exclusive=True, thread=True)
    def load_source(self, source: str) -> None:
        try:
            fetchers = {
                "random": lambda: self.client.random_songs(self.config.ui.page_size),
                "newest": lambda: self.client.newest_albums(self.config.ui.page_size),
                "frequent": lambda: self.client.frequent_albums(self.config.ui.page_size),
                "recent": lambda: self.client.recent_albums(self.config.ui.page_size),
                "starred": self.client.starred_songs,
            }
            songs = fetchers.get(source, lambda: [])()
            self.call_from_thread(self._populate, songs)
        except Exception as exc:
            self.call_from_thread(self._show_error, str(exc))

    @work(exclusive=True, thread=True)
    def run_search(self, query: str) -> None:
        try:
            songs = self.client.search(query, self.config.ui.page_size)
            self.call_from_thread(self._populate, songs)
        except Exception as exc:
            self.call_from_thread(self._show_error, str(exc))

    def _populate(self, songs: list[dict]) -> None:
        lv = self.query_one("#track_list", ListView)
        lv.clear()
        if not songs:
            self.query_one("#track_info", Static).update("[yellow]No tracks found.[/yellow]")
            return
        for song in songs:
            lv.append(TrackItem(song))
        lv.focus()

    def _show_error(self, msg: str) -> None:
        self.query_one("#track_info", Static).update(f"[red]Error:\n{msg}[/red]")
        self.notify(msg, severity="error", title="Connection error")

    # ---------------- Playback ----------------

    def play_item(self, item: TrackItem) -> None:
        try:
            stream_url = self.client.stream_url(item.track_id)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.backend.play(stream_url)
        self.current_item = item
        self._update_track_info()

    def _update_track_info(self, state: str = "▶ Now playing") -> None:
        item = self.current_item
        if item is None:
            self.query_one("#track_info", Static).update("No track selected")
            return
        star = "⭐ " if item.starred else ""
        self.query_one("#track_info", Static).update(
            f"{state}:\n[bold green]{star}{item.title}[/bold green]\n[dim]{item.artist}[/dim]"
        )

    # ---------------- Events ----------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TrackItem):
            self.play_item(event.item)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only update results on Enter (stable, avoids hammering the server).
        query = event.value.strip()
        if query:
            self.run_search(query)
        else:
            # Enter on an empty box -> back to the selected source list.
            self.load_source(self.query_one("#source_select", Select).value)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value is not None:
            self.query_one("#search", Input).value = ""
            self.load_source(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "btn_stop": self.action_stop,
            "btn_toggle": self.action_toggle_pause,
            "btn_next": self.action_next,
            "btn_prev": self.action_prev,
            "btn_star": self.action_toggle_star,
            "btn_reload": self.action_reload,
        }
        handler = handlers.get(event.button.id)
        if handler:
            handler()

    # ---------------- Actions ----------------

    def action_stop(self) -> None:
        self.backend.stop()
        self.current_item = None
        self.query_one("#track_info", Static).update("[yellow]Stopped[/yellow]")

    def action_toggle_pause(self) -> None:
        if self.current_item is not None:
            paused = self.backend.toggle_pause()
            self._update_track_info("⏸ Paused" if paused else "▶ Now playing")
        else:
            self._play_highlighted()

    def _play_highlighted(self) -> None:
        lv = self.query_one("#track_list", ListView)
        if isinstance(lv.highlighted_child, TrackItem):
            self.play_item(lv.highlighted_child)

    def _move(self, delta: int) -> None:
        lv = self.query_one("#track_list", ListView)
        if lv.index is None or not lv.children:
            return
        lv.index = (lv.index + delta) % len(lv.children)
        if isinstance(lv.highlighted_child, TrackItem):
            self.play_item(lv.highlighted_child)

    def action_next(self) -> None:
        self._move(1)

    def action_prev(self) -> None:
        self._move(-1)

    def action_reload(self) -> None:
        # Reload always returns to the selected source list and clears the search box.
        self.query_one("#search", Input).value = ""
        self.load_source(self.query_one("#source_select", Select).value)
        self.notify("🔄 Reloading...", timeout=1.5)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_seek_fwd(self) -> None:
        self._seek(10)

    def action_seek_back(self) -> None:
        self._seek(-10)

    def _seek(self, seconds: float) -> None:
        if self.backend.seek(seconds) is None:
            self.notify("The current backend does not support seeking (mpv required).",
                        severity="warning", timeout=2)

    def action_volume_up(self) -> None:
        self._set_volume(self.volume + 5)

    def action_volume_down(self) -> None:
        self._set_volume(self.volume - 5)

    def _set_volume(self, value: int) -> None:
        self.volume = max(0, min(100, value))
        self.backend.set_volume(self.volume)
        self.query_one("#status_bar", Static).update(self._status_text())
        bars = "█" * (self.volume // 10) + "░" * (10 - self.volume // 10)
        suffix = "" if self.backend.supports_live else "  (applies to next track)"
        self.notify(f"🔊 {bars} {self.volume}%{suffix}", timeout=1.5)

    @work(thread=True)
    def action_toggle_star(self) -> None:
        item = self.current_item or self._highlighted()
        if item is None:
            return
        try:
            if item.starred:
                self.client.unstar(item.track_id)
            else:
                self.client.star(item.track_id)
            item.starred = not item.starred
            self.call_from_thread(self.notify,
                                  f"{'Starred' if item.starred else 'Unstarred'}: {item.title}")
        except Exception as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    def _highlighted(self) -> TrackItem | None:
        lv = self.query_one("#track_list", ListView)
        return lv.highlighted_child if isinstance(lv.highlighted_child, TrackItem) else None

    def on_unmount(self) -> None:
        try:
            self.backend.quit()
        except Exception:
            pass


def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        sys.exit(1)
    NavidromePlayer(config).run()


if __name__ == "__main__":
    main()
