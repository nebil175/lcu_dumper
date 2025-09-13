"""
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class LockfileInfo:
    name: str
    pid: int
    port: int
    password: str
    protocol: str
    path: str


def _candidate_paths_windows() -> List[str]:
    paths: List[str] = []
    local_app = os.environ.get("LocalAppData") or os.environ.get("LOCALAPPDATA")
    if local_app:
        paths.append(os.path.join(local_app, "Riot Games", "Riot Client", "Config", "lockfile"))
        paths.append(os.path.join(local_app, "League of Legends", "lockfile"))
    return paths


def _candidate_paths_macos() -> List[str]:
    home = os.path.expanduser("~")
    return [os.path.join(home, "Library", "Application Support", "League of Legends", "lockfile")]


def _candidate_paths_linux() -> List[str]:
    home = os.path.expanduser("~")
    return [os.path.join(home, ".local", "share", "League of Legends", "lockfile")]


def _all_candidate_paths() -> List[str]:
    paths: List[str] = []
    if os.name == "nt":
        paths.extend(_candidate_paths_windows())
    else:
        # try all known OS paths for cross-platform support
        paths.extend(_candidate_paths_macos())
        paths.extend(_candidate_paths_linux())
    return paths


def find_lockfile() -> Optional[str]:
    for p in _all_candidate_paths():
        if os.path.isfile(p):
            return p
    return None


def parse_lockfile(path: str) -> LockfileInfo:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    parts = content.split(":")
    if len(parts) != 5:
        raise ValueError(f"Unexpected lockfile format in {path!r}")
    name, pid_s, port_s, password, protocol = parts
    try:
        pid = int(pid_s)
        port = int(port_s)
    except ValueError as e:
        raise ValueError("Invalid pid/port in lockfile") from e
    return LockfileInfo(
        name=name,
        pid=pid,
        port=port,
        password=password,
        protocol=protocol,
        path=path,
    )

