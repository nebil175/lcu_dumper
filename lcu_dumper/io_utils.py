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

import fnmatch
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class SupportsColor:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    @staticmethod
    def detect() -> "SupportsColor":
        # Simple detection; avoid dependencies like colorama
        enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        return SupportsColor(enabled)


def colorize(text: str, color: str, supports_color: SupportsColor) -> str:
    if not supports_color.enabled:
        return text
    colors = {
        "red": "\x1b[31m",
        "green": "\x1b[32m",
        "yellow": "\x1b[33m",
        "blue": "\x1b[34m",
        "magenta": "\x1b[35m",
        "cyan": "\x1b[36m",
        "white": "\x1b[37m",
    }
    reset = "\x1b[0m"
    return f"{colors.get(color, '')}{text}{reset}"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_file(path: str, data: Any) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def redacted(value: str) -> str:
    if not value:
        return value
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]


def print_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths: List[int] = [len(h) for h in headers]
    for row in rows:
        for i, col in enumerate(row):
            widths[i] = max(widths[i], len(col))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def is_regex_pattern(pat: str) -> bool:
    # Heuristic: treat as regex if looks like one
    return bool(re.search(r"[\^\$\[\]\(\)\|\+\?]", pat)) or pat.startswith("(?")


def match_any(path: str, patterns: Sequence[str]) -> bool:
    for p in patterns:
        if not p:
            continue
        if is_regex_pattern(p):
            try:
                if re.search(p, path):
                    return True
            except re.error:
                # Fall back to glob if invalid regex
                if fnmatch.fnmatch(path, p):
                    return True
        else:
            # Support ** with fnmatch by translating
            if fnmatch.fnmatch(path, p):
                return True
    return False


def jitter_sleep_ms_range(jitter_ms: Tuple[int, int]) -> float:
    a, b = jitter_ms
    return random.uniform(a, b) / 1000.0


def load_params_file(path: str) -> Dict[str, List[Dict[str, object]]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("params file must be a JSON object")
    out: Dict[str, List[Dict[str, object]]] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, list):
            raise ValueError("params file entries must map string path to list of objects")
        list_items: List[Dict[str, object]] = []
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("each params entry must be an object")
            list_items.append(item)
        out[k] = list_items
    return out


def sanitize_path(path: str) -> str:
    # Remove leading slash
    if path.startswith("/"):
        path = path[1:]
    # For Windows compatibility, avoid colon and other invalid chars in filenames
    safe = re.sub(r"[^A-Za-z0-9._\-\/]", "_", path)
    return safe


