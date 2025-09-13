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

import argparse
import json
import os
import re
import signal
import sys
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

import requests

from . import __version__
from .discovery import Endpoint, discover_endpoints
from .io_utils import (
    SupportsColor,
    colorize,
    ensure_dir,
    load_params_file,
    now_iso,
    print_table,
    redacted,
    write_json_file,
)
from .lockfile import LockfileInfo, find_lockfile, parse_lockfile
from .runner import DumpPlan, build_dump_plan, run_dump_plan


def _default_output_dir() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(".", "lcu_dump", stamp)


def _parse_methods(value: str) -> List[str]:
    methods = [m.strip().upper() for m in value.split(",") if m.strip()]
    valid = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    for m in methods:
        if m not in valid:
            raise argparse.ArgumentTypeError(f"Invalid HTTP method: {m}")
    return methods


def build_parser() -> argparse.ArgumentParser:
    cheatsheet = (
        "CLI Cheatsheet\n"
        "- --include: Glob/regex path filters (e.g., /lol-summoner/**, ^/lol-chat/.*).\n"
        "- --exclude: Glob/regex filters to exclude.\n"
        "- --methods: Comma-separated HTTP methods (default: GET). Write methods blocked unless --allow-write.\n"
        "- --allow-write: Enable POST/PUT/PATCH/DELETE (dangerous; use with care).\n"
        "- --output: Output directory (default: ./lcu_dump/{YYYYMMDD_HHMMSS}).\n"
        "- --concurrency: Worker threads (default: 8).\n"
        "- --timeout: Per-request timeout seconds (default: 5).\n"
        "- --retry: Total attempts per request with backoff (default: 2).\n"
        "- --params-file: JSON file with path parameter expansions.\n"
        "- --from-index: Use an existing endpoints_index.json (or pruned) instead of discovering.\n"
        "- --analyze <dump_dir>: Analyze a dump; writes active_endpoints_index.json and params.autogen.json.\n"
        "- --auto-params-from <dump_dir>: Use or generate params.autogen.json from a prior dump.\n"
        "- --auto-params-limit N: Max param combos per templated path when auto-generating (default: 5).\n"
        "- --auto-params-mode {zip|cartesian}: Combine placeholder values by index (zip) or cartesian (capped by limit).\n"
        "- --per-endpoint-dir: Store each endpoint with response.json and meta.json in its own folder.\n"
        "- --dry-run: Show targets without making requests.\n"
        "- --list: Print discovered endpoints (METHOD, PATH) and exit.\n"
        "- --show <regex>: Preview final targets after filters (applies to METHOD PATH).\n"
        "- Env: LCU_DUMPER_INSECURE=1 can force insecure mode (default already insecure)."
    )
    p = argparse.ArgumentParser(
        prog="lcu_dumper",
        description=(
            "Dump League Client (LCU) API endpoints to timestamped JSON files."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=cheatsheet,
    )

    p.add_argument(
        "--include",
        action="append",
        default=[],
        help="Glob/regex path filters to include (e.g., /lol-summoner/** or ^/lol-chat/.*).",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Glob/regex path filters to exclude.",
    )
    p.add_argument(
        "--methods",
        type=_parse_methods,
        default=["GET"],
        help="Comma-separated HTTP methods to include (default: GET).",
    )
    p.add_argument(
        "--allow-write",
        action="store_true",
        help="Allow mutating methods (POST/PUT/PATCH/DELETE). Default is read-only.",
    )
    p.add_argument(
        "--output",
        default=_default_output_dir(),
        help="Output directory (default: ./lcu_dump/{YYYYMMDD_HHMMSS}).",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Number of concurrent requests (default: 8).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-request timeout seconds (default: 5).",
    )
    p.add_argument(
        "--retry",
        type=int,
        default=2,
        help="Total attempts per request with backoff (default: 2).",
    )
    p.add_argument(
        "--params-file",
        default=None,
        help="JSON file with path parameter expansions.",
    )
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS certificate verification (default behavior).",
    )
    p.add_argument(
        "--from-index",
        default=None,
        help="Load endpoints from a previously saved endpoints_index.json instead of discovering.",
    )
    p.add_argument(
        "--analyze",
        default=None,
        help="Analyze an existing dump directory: summarize statuses, auto-generate params, and write pruned indexes.",
    )
    p.add_argument(
        "--auto-params-from",
        default=None,
        help="Mine params from a previous dump directory (uses params.autogen.json or generates it).",
    )
    p.add_argument(
        "--auto-params-limit",
        type=int,
        default=5,
        help="Max param combinations per templated path when auto-generating params (default: 5).",
    )
    p.add_argument(
        "--auto-params-mode",
        choices=["zip", "cartesian"],
        default="zip",
        help="How to combine placeholder values for templated paths when auto-generating params (default: zip).",
    )
    p.add_argument(
        "--per-endpoint-dir",
        action="store_true",
        help="Write each endpoint into its own folder with response.json and meta.json",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List the exact set of requests to perform without executing.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="Discover and print all available endpoints (method + path).",
    )
    p.add_argument(
        "--show",
        default=None,
        help="Regex to preview targets after filters (applied to METHOD PATH).",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"lcu_dumper {__version__}",
    )

    return p


def _enforce_write_safety(methods: Sequence[str], allow_write: bool) -> Tuple[List[str], Optional[str]]:
    selected = list(methods)
    if allow_write:
        return selected, None
    unsafe = {"POST", "PUT", "PATCH", "DELETE"}
    removed = [m for m in selected if m in unsafe]
    selected = [m for m in selected if m not in unsafe]
    warn: Optional[str] = None
    if removed:
        warn = (
            "Write methods excluded by default: " + ", ".join(sorted(set(removed))) +
            ". Use --allow-write to enable."
        )
    if not selected:
        selected = ["GET"]
    return selected, warn


def _make_session(verify: bool, auth: Tuple[str, str]) -> requests.Session:
    s = requests.Session()
    s.verify = verify
    s.auth = auth  # BasicAuth tuple
    s.headers.update({
        "Accept": "application/json",
        "User-Agent": f"lcu_dumper/{__version__}",
    })
    return s


def _print_discovered(endpoints: List[Endpoint]) -> None:
    rows = [(e.method, e.path) for e in sorted(endpoints, key=lambda x: (x.method, x.path))]
    print_table(["METHOD", "PATH"], rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    supports_color = SupportsColor.detect()

    # Default to insecure mode to avoid local self-signed cert issues
    env_insecure = str(os.environ.get("LCU_DUMPER_INSECURE", "")).strip().lower() in {"1", "true", "yes", "on"}
    insecure_effective = True  # always run without TLS verification by default

    if insecure_effective:
        try:
            # Justification: urllib3 is bundled within requests; disabling warnings avoids noisy insecure HTTPS warnings
            import urllib3  # type: ignore

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        print(colorize("[warn] TLS verification disabled (default)", "yellow", supports_color))

    # If analysis only is requested, do not touch LCU
    if args.analyze:
        from .analyze import summarize_from_dump_dir, write_analysis_outputs, load_endpoints_index
        dump_dir = args.analyze
        try:
            statuses, candidates = summarize_from_dump_dir(dump_dir)
        except Exception as e:
            print(colorize(f"[fatal] Analyze failed: {e}", "red", supports_color))
            return 1
        # Gather active and 404 endpoints if endpoints_index.json is present and per-endpoint meta exists
        active: List[tuple[str, str]] = []
        not_found: List[tuple[str, str]] = []
        # Walk meta files under dump dir
        for root, _dirs, files in os.walk(dump_dir):
            for f in files:
                if f.endswith("meta.json"):
                    try:
                        meta = json.load(open(os.path.join(root, f), "r", encoding="utf-8"))
                        status = int(meta.get("statusCode", 0))
                    except Exception:
                        continue
                    # Derive method+path from folder structure: <dump>/<METHOD>/<...>/<name>/meta.json
                    rel = os.path.relpath(os.path.join(root, f), dump_dir)
                    parts = rel.split(os.sep)
                    if len(parts) < 3:
                        continue
                    method = parts[0]
                    if f == "meta.json":
                        # per-endpoint-dir mode: leaf is the folder name
                        path_part = os.path.join(*parts[1:-1])
                        reconstructed_path = "/" + path_part.replace("\\", "/")
                    elif f.endswith(".meta.json"):
                        # flat mode: filename contains endpoint leaf
                        leaf = f[: -len(".meta.json")]
                        path_part = os.path.join(*parts[1:-1], leaf)
                        reconstructed_path = "/" + path_part.replace("\\", "/")
                    else:
                        continue
                    if status and 200 <= status < 400:
                        active.append((method, reconstructed_path))
                    elif status == 404:
                        not_found.append((method, reconstructed_path))
        write_analysis_outputs(
            dump_dir,
            active,
            not_found,
            candidates,
            limit_per_path=max(1, args.auto_params_limit),
            mode=args.auto_params_mode,
        )
        # Print brief summary
        print(colorize(f"[analyze] statuses: {statuses}", "cyan", supports_color))
        if candidates:
            print(colorize(f"[analyze] candidate keys: {', '.join(sorted(candidates.keys()))}", "cyan", supports_color))
        print(colorize(f"[done] Wrote analysis files alongside {dump_dir}", "green", supports_color))
        return 0

    # Step 1: find and parse lockfile
    lock_path = find_lockfile()
    if not lock_path:
        print(colorize("[fatal] Could not find LCU lockfile. Is the client running?", "red", supports_color))
        return 1

    try:
        info: LockfileInfo = parse_lockfile(lock_path)
    except Exception as e:  # pragma: no cover - safety
        print(colorize(f"[fatal] Failed to parse lockfile: {e}", "red", supports_color))
        return 1

    verify = not insecure_effective
    base_url = f"https://127.0.0.1:{info.port}"
    session = _make_session(verify=verify, auth=("riot", info.password))

    # Step 2: discover endpoints
    endpoints: List[Endpoint]
    if args.from_index:
        # Load endpoints from given index file
        try:
            with open(args.from_index, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("index must be a JSON array")
            endpoints = [Endpoint(str(e.get("method", "GET")).upper(), str(e.get("path"))) for e in data]
        except Exception as e:
            print(colorize(f"[fatal] Failed to load --from-index file: {e}", "red", supports_color))
            return 1
    else:
        try:
            endpoints = discover_endpoints(session, base_url, timeout=args.timeout)
        except requests.exceptions.SSLError:
            print(colorize("[fatal] TLS error. Try --insecure if you trust the local client.", "red", supports_color))
            return 1
        except requests.RequestException as e:
            print(colorize(f"[fatal] Failed to connect to LCU API: {e}", "red", supports_color))
            return 1

    # Persist endpoints index immediately into output directory
    output_dir = args.output
    ensure_dir(output_dir)
    write_json_file(os.path.join(output_dir, "endpoints_index.json"), [e.as_dict() for e in endpoints])

    if args.list:
        _print_discovered(endpoints)
        return 0

    # Enforce method safety
    methods, warn = _enforce_write_safety(args.methods, args.allow_write)
    if warn:
        print(colorize(f"[warn] {warn}", "yellow", supports_color))

    # Load params file if provided
    params_map = None
    if args.params_file:
        try:
            params_map = load_params_file(args.params_file)
        except Exception as e:
            print(colorize(f"[fatal] Failed to load params file: {e}", "red", supports_color))
            return 1
    # Auto params mined from previous dump
    if args.auto_params_from:
        auto_dir = args.auto_params_from
        auto_params_path = os.path.join(auto_dir, "params.autogen.json")
        if not os.path.isfile(auto_params_path):
            # attempt to generate it quickly
            try:
                from .analyze import summarize_from_dump_dir, write_analysis_outputs, load_endpoints_index
                statuses, candidates = summarize_from_dump_dir(auto_dir)
                # Build using discovered endpoints if available, else skip
                idx_path = os.path.join(auto_dir, "endpoints_index.json")
                eps = load_endpoints_index(idx_path) if os.path.isfile(idx_path) else []
                write_analysis_outputs(
                    auto_dir,
                    [],
                    [],
                    candidates,
                    endpoints_for_params=eps,
                    limit_per_path=max(1, args.auto_params_limit),
                    mode=args.auto_params_mode,
                )
            except Exception:
                pass
        if os.path.isfile(auto_params_path):
            try:
                auto_params = load_params_file(auto_params_path)
                if params_map is None:
                    params_map = auto_params
                else:
                    # merge lists, avoid exact duplicates
                    for k, v in auto_params.items():
                        existing = params_map.get(k, [])
                        for item in v:
                            if item not in existing:
                                existing.append(item)
                        params_map[k] = existing
            except Exception as e:
                print(colorize(f"[warn] Failed to load auto params: {e}", "yellow", supports_color))

    # Build dump plan (filtering + param expansion)
    plan: DumpPlan = build_dump_plan(
        endpoints=endpoints,
        includes=args.include,
        excludes=args.exclude,
        methods=methods,
        params=params_map,
        base_output_dir=output_dir,
        per_endpoint_dir=args.per_endpoint_dir,
    )

    if args.show is not None:
        try:
            rx = re.compile(args.show)
        except re.error as e:
            print(colorize(f"[fatal] Invalid --show regex: {e}", "red", supports_color))
            return 1
        rows: List[Tuple[str, str]] = []
        for item in plan.items:
            s = f"{item.method} {item.rendered_path}"
            if rx.search(s):
                rows.append((item.method, item.rendered_path))
        print_table(["METHOD", "PATH"], rows)
        if args.dry_run:
            return 0

    if args.dry_run:
        rows = [(i.method, i.rendered_path) for i in plan.items]
        print_table(["METHOD", "PATH"], rows)
        return 0

    # Graceful SIGINT
    interrupted = {"flag": False}

    def _handle_sigint(signum: int, frame: object | None) -> None:  # pragma: no cover - runtime only
        if not interrupted["flag"]:
            print(colorize("\n[info] SIGINT received. Finishing in-flight requests...", "yellow", supports_color))
            interrupted["flag"] = True
        else:
            print(colorize("\n[info] Force exit.", "red", supports_color))
            os._exit(130)

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        pass

    # Run dump plan
    results = run_dump_plan(
        plan=plan,
        make_session=lambda: _make_session(verify=verify, auth=("riot", info.password)),
        base_url=base_url,
        timeout=args.timeout,
        attempts=args.retry,
        concurrency=args.concurrency,
        jitter_ms=(50, 150),
        supports_color=supports_color,
        cancel_flag=interrupted,
    )

    # Summarize
    total = results.total
    ok = results.ok
    failed = results.failed
    skipped = results.skipped
    print(colorize(
        f"[done] total={total} ok={ok} failed={failed} skipped={skipped} output={output_dir}",
        "green" if failed == 0 else "yellow" if ok > 0 else "red",
        supports_color,
    ))

    if failed > 0 and ok > 0:
        return 2
    if failed > 0 and ok == 0:
        return 1
    return 0
