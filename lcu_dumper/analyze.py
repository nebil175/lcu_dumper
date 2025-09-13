"""
MIT License

Utility to analyze previous dump directories to:
- summarize statuses
- auto-generate params for templated endpoints by mining IDs from existing JSON
- write pruned endpoint indexes (active endpoints only)
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import itertools

from .discovery import Endpoint
from .io_utils import ensure_dir, write_json_file


def _iter_files(root: str, suffix: str) -> Iterable[str]:
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(suffix.lower()):
                yield os.path.join(base, f)


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze_statuses(dump_dir: str) -> Dict[str, int]:
    counts: Counter[str] = Counter()
    for meta_path in _iter_files(dump_dir, "meta.json"):
        try:
            meta = _load_json(meta_path)
            code = int(meta.get("statusCode", 0))
            key = str(code)
        except Exception:
            key = "invalid"
        counts[key] += 1
    return dict(counts)


_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")

_CANDIDATE_KEYS: Tuple[str, ...] = (
    "summonerId",
    "accountId",
    "puuid",
    "id",
    "conversationId",
    "lobbyId",
    "gameId",
    "matchId",
    "championId",
)


def _collect_candidates_from_value(key: str, value: Any, out: Dict[str, Set[str]]) -> None:
    if isinstance(value, (str, int)):
        sval = str(value)
        if key == "id":
            # favor GUID-like strings for generic {id}
            if isinstance(value, str) and _GUID_RE.match(value):
                out[key].add(value)
        else:
            out[key].add(sval)


def _walk_json(obj: Any, out: Dict[str, Set[str]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _CANDIDATE_KEYS:
                _collect_candidates_from_value(k, v, out)
            _walk_json(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json(v, out)


def mine_param_candidates(dump_dir: str) -> Dict[str, List[str]]:
    """Scan response.json files to collect likely parameter values by key name."""
    out: Dict[str, Set[str]] = {k: set() for k in _CANDIDATE_KEYS}
    for resp_path in _iter_files(dump_dir, "response.json"):
        try:
            data = _load_json(resp_path)
        except Exception:
            continue
        # content stored as {"data": ...} or {"text": ...}
        if isinstance(data, dict) and "data" in data:
            _walk_json(data["data"], out)
    return {k: list(sorted(vals)) for k, vals in out.items() if vals}


def load_endpoints_index(path: str) -> List[Endpoint]:
    data = _load_json(path)
    if not isinstance(data, list):
        raise ValueError("endpoints_index.json must be a list")
    eps: List[Endpoint] = []
    for e in data:
        if not isinstance(e, dict):
            continue
        method = str(e.get("method", "GET")).upper()
        path = str(e.get("path", ""))
        if not path:
            continue
        eps.append(Endpoint(method, path))
    return eps


def _path_placeholders(path: str) -> List[str]:
    return re.findall(r"\{([^{}]+)\}", path)


def build_autoparams_for_endpoints(
    endpoints: Sequence[Endpoint],
    candidates: Mapping[str, Sequence[str]],
    limit_per_path: int = 5,
    mode: str = "zip",
) -> Dict[str, List[Dict[str, object]]]:
    out: Dict[str, List[Dict[str, object]]] = {}
    for e in endpoints:
        phs = _path_placeholders(e.path)
        if not phs:
            continue
        # Build cross-product using available candidate sets for placeholders
        # For simplicity, if any placeholder lacks candidates, skip that path
        lists: List[List[str]] = []
        for ph in phs:
            vals = candidates.get(ph)
            if not vals:
                # fallback for generic {id}
                if ph == "id":
                    vals = candidates.get("id", [])
                else:
                    vals = []
            if not vals:
                lists = []
                break
            lists.append(list(vals)[:limit_per_path])
        if not lists:
            continue
        path_params: List[Dict[str, object]] = []
        if mode == "cartesian":
            # Generate up to limit_per_path combinations from the cartesian product
            prod = itertools.product(*lists)
            for i, combo in enumerate(prod):
                if i >= limit_per_path:
                    break
                params = {phs[j]: combo[j] for j in range(len(phs))}
                path_params.append(params)
        else:
            # zip mode: align by index; cap at limit_per_path
            min_len = min(len(l) for l in lists)
            take = min(min_len, limit_per_path)
            for idx in range(take):
                combo = [lists[j][idx] for j in range(len(lists))]
                params = {phs[j]: combo[j] for j in range(len(phs))}
                path_params.append(params)
        if path_params:
            out[e.path] = path_params
    return out


def write_analysis_outputs(
    dump_dir: str,
    active_endpoints: List[Tuple[str, str]],
    not_found_endpoints: List[Tuple[str, str]],
    candidates: Mapping[str, Sequence[str]],
    endpoints_for_params: Optional[Sequence[Endpoint]] = None,
    limit_per_path: int = 5,
    mode: str = "zip",
) -> None:
    if endpoints_for_params is None:
        # Try to load from endpoints_index.json in the dump dir
        idx_path = os.path.join(dump_dir, "endpoints_index.json")
        if os.path.isfile(idx_path):
            try:
                endpoints_for_params = load_endpoints_index(idx_path)
            except Exception:
                endpoints_for_params = []
        else:
            endpoints_for_params = []

    active = [{"method": m, "path": p} for m, p in sorted(set(active_endpoints))]
    pruned = os.path.join(dump_dir, "active_endpoints_index.json")
    write_json_file(pruned, active)

    if not_found_endpoints:
        nf = [{"method": m, "path": p} for m, p in sorted(set(not_found_endpoints))]
        write_json_file(os.path.join(dump_dir, "not_found_endpoints.json"), nf)

    # Auto params
    autoparams = build_autoparams_for_endpoints(endpoints_for_params or [], candidates, limit_per_path=limit_per_path, mode=mode)
    if autoparams:
        write_json_file(os.path.join(dump_dir, "params.autogen.json"), autoparams)


def summarize_from_dump_dir(dump_dir: str) -> Tuple[Dict[str, int], Dict[str, List[str]]]:
    statuses = analyze_statuses(dump_dir)
    candidates = mine_param_candidates(dump_dir)
    return statuses, candidates
