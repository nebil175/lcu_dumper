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

import concurrent.futures
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import requests

from .discovery import Endpoint
from .io_utils import (
    SupportsColor,
    colorize,
    ensure_dir,
    jitter_sleep_ms_range,
    match_any,
    now_iso,
    sanitize_path,
    write_json_file,
)


@dataclass(frozen=True)
class DumpItem:
    method: str
    rendered_path: str
    output_json: str
    output_meta: str


@dataclass(frozen=True)
class DumpPlan:
    items: List[DumpItem]


@dataclass(frozen=True)
class DumpResult:
    total: int
    ok: int
    failed: int
    skipped: int


def _path_needs_params(path: str) -> bool:
    return "{" in path and "}" in path


def _render_path(path_template: str, params: Dict[str, object]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"Missing parameter '{key}' for path {path_template}")
        val = params[key]
        return quote(str(val), safe="")

    return re.sub(r"\{([^{}]+)\}", repl, path_template)


def build_dump_plan(
    *,
    endpoints: Sequence[Endpoint],
    includes: Sequence[str],
    excludes: Sequence[str],
    methods: Sequence[str],
    params: Optional[Dict[str, List[Dict[str, object]]]],
    base_output_dir: str,
    per_endpoint_dir: bool = False,
) -> DumpPlan:
    # Filter endpoints
    filtered: List[Endpoint] = []
    for e in endpoints:
        if e.method not in methods:
            continue
        if includes and not match_any(e.path, includes):
            continue
        if excludes and match_any(e.path, excludes):
            continue
        filtered.append(e)

    items: List[DumpItem] = []
    for e in filtered:
        if _path_needs_params(e.path):
            if not params or e.path not in params:
                # skip templated endpoints with no provided params
                continue
            for param_set in params[e.path]:
                try:
                    rendered = _render_path(e.path, param_set)
                except KeyError:
                    # missing param; skip this param_set
                    continue
                out_json, out_meta = _output_paths(base_output_dir, e.method, rendered, per_endpoint_dir)
                items.append(DumpItem(e.method, rendered, out_json, out_meta))
        else:
            out_json, out_meta = _output_paths(base_output_dir, e.method, e.path, per_endpoint_dir)
            items.append(DumpItem(e.method, e.path, out_json, out_meta))

    return DumpPlan(items=items)


def _output_paths(base: str, method: str, path: str, per_endpoint_dir: bool) -> Tuple[str, str]:
    clean = sanitize_path(path)
    output_dir = os.path.join(base, method, os.path.dirname(clean))
    filename = os.path.basename(clean) or "index"
    if per_endpoint_dir:
        endpoint_dir = os.path.join(output_dir, filename)
        json_path = os.path.join(endpoint_dir, "response.json")
        meta_path = os.path.join(endpoint_dir, "meta.json")
    else:
        json_path = os.path.join(output_dir, f"{filename}.json")
        meta_path = os.path.join(output_dir, f"{filename}.meta.json")
    return json_path, meta_path


def _limited_request_headers(req: requests.PreparedRequest) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if req.headers is None:
        return out
    allow = {"Accept", "Content-Type", "User-Agent"}
    for k, v in req.headers.items():
        if k in allow:
            out[k] = v
    return out


def _attempt_request(
    session: requests.Session,
    base_url: str,
    item: DumpItem,
    timeout: float,
    attempts: int,
) -> Tuple[Optional[Dict[str, object]], Dict[str, object]]:
    # returns (json_response or None, meta)
    last_err: Optional[str] = None
    for attempt in range(1, max(1, attempts) + 1):
        t0 = time.perf_counter()
        url = base_url + item.rendered_path
        req = requests.Request(method=item.method, url=url)
        prepped = session.prepare_request(req)
        try:
            resp = session.send(prepped, timeout=timeout)
            duration_ms = int((time.perf_counter() - t0) * 1000)
            meta: Dict[str, object] = {
                "timestamp": now_iso(),
                "statusCode": resp.status_code,
                "durationMs": duration_ms,
                "requestHeaders": _limited_request_headers(prepped),
            }

            text = resp.text
            try:
                content = resp.json()
                if not isinstance(content, (dict, list, int, float, str, bool)):
                    content = {"value": content}
                return {"data": content}, meta
            except ValueError:
                # Not JSON; store as text
                return {"text": text, "contentType": resp.headers.get("Content-Type", "")}, meta
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            # backoff: 0.5s * (attempt) + jitter
            time.sleep(0.5 * attempt)
            continue
    # final failure
    meta = {
        "timestamp": now_iso(),
        "statusCode": 0,
        "durationMs": 0,
        "requestHeaders": {},
        "errors": last_err or "unknown error",
    }
    return None, meta


def run_dump_plan(
    *,
    plan: DumpPlan,
    make_session: Callable[[], requests.Session],
    base_url: str,
    timeout: float,
    attempts: int,
    concurrency: int,
    jitter_ms: Tuple[int, int],
    supports_color: SupportsColor,
    cancel_flag: Dict[str, bool],
) -> DumpResult:
    if not plan.items:
        print(colorize("[info] No endpoints to process after filtering.", "yellow", supports_color))
        return DumpResult(total=0, ok=0, failed=0, skipped=0)

    total = len(plan.items)
    ok = 0
    failed = 0
    skipped = 0

    # Use thread-local session objects
    local = threading.local()

    def get_session() -> requests.Session:
        s = getattr(local, "session", None)
        if s is None:
            s = make_session()
            setattr(local, "session", s)
        return s

    def worker(item: DumpItem) -> Tuple[DumpItem, bool, Optional[str]]:
        if cancel_flag.get("flag"):
            return item, False, "cancelled"
        # polite jitter
        time.sleep(jitter_sleep_ms_range(jitter_ms))
        session = get_session()
        content, meta = _attempt_request(session, base_url, item, timeout, attempts)

        ensure_dir(os.path.dirname(item.output_json) or ".")
        if content is None:
            # write meta with error
            write_json_file(item.output_meta, meta)
            return item, False, str(meta.get("errors"))

        # write JSON and meta
        try:
            write_json_file(item.output_json, content)
            write_json_file(item.output_meta, meta)
            return item, True, None
        except Exception as e:  # pragma: no cover - filesystem
            meta["errors"] = f"write: {type(e).__name__}: {e}"
            write_json_file(item.output_meta, meta)
            return item, False, str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures: List[concurrent.futures.Future[Tuple[DumpItem, bool, Optional[str]]]] = []
        for item in plan.items:
            futures.append(ex.submit(worker, item))

        for fut in concurrent.futures.as_completed(futures):
            try:
                item, success, err = fut.result()
            except Exception as e:  # pragma: no cover - unexpected
                failed += 1
                print(colorize(f"[fail] {e}", "red", supports_color))
                continue
            if success:
                ok += 1
                print(colorize(f"[ok] {item.method} {item.rendered_path}", "green", supports_color))
            else:
                if err == "cancelled":
                    skipped += 1
                    print(colorize(f"[skip] {item.method} {item.rendered_path} (cancelled)", "yellow", supports_color))
                else:
                    failed += 1
                    print(colorize(f"[fail] {item.method} {item.rendered_path}: {err}", "red", supports_color))

    return DumpResult(total=total, ok=ok, failed=failed, skipped=skipped)
