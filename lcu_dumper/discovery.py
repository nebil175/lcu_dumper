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

import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import requests


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str

    def as_dict(self) -> Dict[str, str]:
        return {"method": self.method, "path": self.path}


SWAGGER_V3_PATH = "/swagger/v3/openapi.json"
SWAGGER_V2_PATH = "/swagger/v2/swagger.json"
HELP_PATH = "/help"


def _parse_paths_object(paths: Dict[str, Dict[str, object]]) -> List[Endpoint]:
    out: List[Endpoint] = []
    for raw_path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method in list(methods.keys()):
            m = str(method).upper()
            if m in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                # normalize path to start with "/"
                p = raw_path if raw_path.startswith("/") else f"/{raw_path}"
                out.append(Endpoint(m, p))
    return out


def _discover_swagger(session: requests.Session, base_url: str, path: str, timeout: float) -> List[Endpoint]:
    url = base_url + path
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected swagger JSON structure")
    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("Swagger JSON missing 'paths'")
    return _parse_paths_object(paths)  # type: ignore[arg-type]


def _discover_help(session: requests.Session, base_url: str, path: str, timeout: float) -> List[Endpoint]:
    url = base_url + path
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    text = r.text
    # Best-effort parse: look for occurrences of METHOD /path in the document
    endpoints: List[Endpoint] = []
    pattern = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s<'\"]+)")
    for m in pattern.finditer(text):
        method, path_found = m.group(1), m.group(2)
        endpoints.append(Endpoint(method, path_found))
    # Deduplicate
    uniq: Dict[Tuple[str, str], Endpoint] = {}
    for e in endpoints:
        uniq[(e.method, e.path)] = e
    if not uniq:
        raise ValueError("Could not parse endpoints from /help output")
    return list(uniq.values())


def discover_endpoints(session: requests.Session, base_url: str, timeout: float = 5.0) -> List[Endpoint]:
    # Try swagger v3
    tried: List[str] = []
    try:
        return _discover_swagger(session, base_url, SWAGGER_V3_PATH, timeout)
    except Exception:
        tried.append(SWAGGER_V3_PATH)
    try:
        return _discover_swagger(session, base_url, SWAGGER_V2_PATH, timeout)
    except Exception:
        tried.append(SWAGGER_V2_PATH)
    # Fallback to /help best-effort
    try:
        return _discover_help(session, base_url, HELP_PATH, timeout)
    except Exception as e:
        raise requests.RequestException(
            f"Failed to discover endpoints via {', '.join(tried + [HELP_PATH])}: {e}"
        )

