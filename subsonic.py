"""Minimal client for the Subsonic / Navidrome API (token auth v2)."""

from __future__ import annotations

import hashlib
import random
import string
from urllib.parse import urlencode

import requests

API_VERSION = "1.16.1"
CLIENT_NAME = "NavidromeTUIPlayer"


class SubsonicError(Exception):
    """Error returned by the Subsonic/Navidrome server."""


class SubsonicClient:
    def __init__(self, url: str, username: str, password: str, timeout: int = 10,
                 stream_format: str = "raw", max_bitrate: int = 0):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        # "raw" + max_bitrate 0 -> Navidrome returns the original file, no transcoding.
        self.stream_format = stream_format
        self.max_bitrate = max_bitrate

    def _auth_params(self) -> dict:
        salt = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": API_VERSION,
            "c": CLIENT_NAME,
            "f": "json",
        }

    def _get(self, endpoint: str, extra: dict | None = None) -> dict:
        params = self._auth_params()
        if extra:
            params.update(extra)
        res = requests.get(f"{self.url}/rest/{endpoint}", params=params, timeout=self.timeout)
        res.raise_for_status()
        body = res.json().get("subsonic-response", {})
        if body.get("status") == "failed":
            err = body.get("error", {})
            raise SubsonicError(err.get("message", "Unknown server error"))
        return body

    def ping(self) -> None:
        self._get("ping")

    def stream_url(self, track_id: str) -> str:
        """Pre-signed stream URL.

        format=raw + maxBitRate=0 asks Navidrome to send the original stream
        (bit-perfect), without re-encoding. Set stream_format="" to use the
        server default.
        """
        params = self._auth_params()
        params["id"] = track_id
        if self.stream_format:
            params["format"] = self.stream_format
        if self.max_bitrate is not None:
            params["maxBitRate"] = self.max_bitrate
        return f"{self.url}/rest/stream?{urlencode(params)}"

    # ---- Track list sources -> list[dict] of songs ----

    def random_songs(self, size: int = 50) -> list[dict]:
        body = self._get("getRandomSongs", {"size": size})
        return body.get("randomSongs", {}).get("song", [])

    def newest_albums(self, size: int = 50) -> list[dict]:
        return self._albums_to_songs("newest", size)

    def frequent_albums(self, size: int = 50) -> list[dict]:
        return self._albums_to_songs("frequent", size)

    def recent_albums(self, size: int = 50) -> list[dict]:
        return self._albums_to_songs("recent", size)

    def starred_songs(self) -> list[dict]:
        body = self._get("getStarred2")
        starred = body.get("starred2", {})
        songs = list(starred.get("song", []))
        songs.extend(self._expand_albums(starred.get("album", [])))
        return songs

    def search(self, query: str, count: int = 50) -> list[dict]:
        body = self._get("search3", {"query": query, "songCount": count,
                                      "albumCount": 0, "artistCount": 0})
        return body.get("searchResult3", {}).get("song", [])

    def star(self, track_id: str) -> None:
        self._get("star", {"id": track_id})

    def unstar(self, track_id: str) -> None:
        self._get("unstar", {"id": track_id})

    # ---- helpers ----

    def _albums_to_songs(self, list_type: str, size: int) -> list[dict]:
        body = self._get("getAlbumList2", {"type": list_type, "size": size})
        albums = body.get("albumList2", {}).get("album", [])
        return self._expand_albums(albums)

    def _expand_albums(self, albums: list[dict]) -> list[dict]:
        songs: list[dict] = []
        for album in albums:
            body = self._get("getAlbum", {"id": album.get("id")})
            songs.extend(body.get("album", {}).get("song", []))
        return songs
