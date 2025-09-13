# LCU Dumper (raw JSON)

<p align="center">
  <img src="lcu_dumper_demo.gif" alt="LCU Dumper demo" width="700" />
  
</p>

LCU Dumper is a cross-platform CLI that connects to the League Client Update (LCU) API via the lockfile and dumps selected endpoints to timestamped JSON files. It is safe by default, fast, and designed for iterative exploration (discover -> analyze -> dump).

## Highlights
- Auto-detects the LCU lockfile on Windows, macOS, Linux.
- Discovers endpoints via Swagger/OpenAPI or `/help`.
- Flexible filters: include/exclude (glob or regex), method filters.
- Param expansion for templated paths via params file or auto-mined params.
- Concurrent requests, per-request timeout, retries with backoff, polite jitter.
- Pretty JSON output plus per-request metadata next to each dump.
- Default read-only; write methods require explicit opt-in.
- Insecure TLS by default (local LCU uses a self-signed cert) so it just works.
- Analyzer to prune dead endpoints and auto-generate params from prior dumps.

## Requirements
- Python 3.10+
- Dependency: `requests`

## Install
```sh
pip install requests
```

## Quick Start (from scratch)
Here are the quickest from-scratch commands to dump:

Help and all flags:
```powershell
python -m lcu_dumper --help
```

Full fresh dump (discover + dump all GET):
```powershell
python -m lcu_dumper --per-endpoint-dir --output .\lcu_dump\full_initial
```

Preview only (no requests):
```powershell
python -m lcu_dumper --dry-run
```

Narrow to chat + summoner only:
```powershell
python -m lcu_dumper --include '^/lol-(chat|summoner)/.*' --per-endpoint-dir --output .\lcu_dump\narrow_initial
```

Include templated endpoint with params:
```powershell
python -m lcu_dumper --include '/lol-summoner/v1/summoners/{summonerId}' --params-file .\params.json --per-endpoint-dir --output .\lcu_dump\params_run
```

Optional: second pass with pruned index and auto-mined params (better hit rate):
```powershell
python -m lcu_dumper --analyze .\lcu_dump\full_initial
python -m lcu_dumper --from-index .\lcu_dump\full_initial\active_endpoints_index.json --auto-params-from .\lcu_dump\full_initial --per-endpoint-dir --output .\lcu_dump\full_pruned
```

Notes
- Insecure TLS is already default; no extra flag needed.
- Tune performance via `--concurrency 8` and `--timeout 5` if desired.

## CLI Cheatsheet
Also available via: `python -m lcu_dumper --help`

- `--include`: Glob/regex path filters (e.g., `/lol-summoner/**`, `^/lol-chat/.*`).
- `--exclude`: Glob/regex filters to exclude.
- `--methods`: Comma-separated HTTP methods (default: `GET`). Write methods blocked unless `--allow-write`.
- `--allow-write`: Enable `POST/PUT/PATCH/DELETE` (dangerous; use with care).
- `--output`: Output directory (default: `./lcu_dump/{YYYYMMDD_HHMMSS}`).
- `--concurrency`: Worker threads (default: `8`).
- `--timeout`: Per-request timeout seconds (default: `5`).
- `--retry`: Total attempts per request with backoff (default: `2`).
- `--params-file`: JSON file with path parameter expansions (see below).
- `--from-index`: Use an existing `endpoints_index.json` (or pruned) instead of discovering.
- `--analyze <dump_dir>`: Analyze a dump; writes `active_endpoints_index.json` and `params.autogen.json`.
- `--auto-params-from <dump_dir>`: Use or generate `params.autogen.json` from a prior dump.
- `--auto-params-limit N`: Max param combos per templated path when auto-generating (default: `5`).
- `--auto-params-mode {zip|cartesian}`: Combine placeholder values by index (zip) or cartesian (capped by limit).
- `--per-endpoint-dir`: Store each endpoint in its own folder with `response.json` and `meta.json`.
- `--dry-run`: Show targets without making requests.
- `--list`: Print discovered endpoints (METHOD, PATH) and exit.
- `--show <regex>`: Preview final targets after filters (applies to `METHOD PATH`).
- Env var: `LCU_DUMPER_INSECURE=1` can force insecure mode (default already insecure).

## Lockfile & Connection
- Windows: `%LocalAppData%\Riot Games\Riot Client\Config\lockfile` and `%LocalAppData%\League of Legends\lockfile`
- macOS: `~/Library/Application Support/League of Legends/lockfile`
- Linux: `~/.local/share/League of Legends/lockfile`
- Lockfile format: `name:pid:port:password:protocol`
- Connects to `https://127.0.0.1:{port}` using Basic Auth `riot:{password}`

## Endpoint Discovery
Tries in order until one works:
- `/swagger/v3/openapi.json`
- `/swagger/v2/swagger.json`
- `/help` (best-effort parse)
Discovered endpoints are written to `<output>/endpoints_index.json`.

## Dump Output Structure
Default (flat files):
- `<output>/<METHOD>/<path>.json`
- `<output>/<METHOD>/<path>.meta.json`

With `--per-endpoint-dir`:
- `<output>/<METHOD>/<path-leaf>/response.json`
- `<output>/<METHOD>/<path-leaf>/meta.json`

Each `meta.json` includes: `statusCode`, `durationMs`, `timestamp`, and a safe subset of `requestHeaders` (no secrets). Errors are recorded if any.

## Parameters File
Use a JSON object mapping templated paths to a list of parameter objects. Example:

```json
{
  "/lol-summoner/v1/summoners/{summonerId}": [{"summonerId": 12345}],
  "/lol-chat/v1/conversations/{id}/messages": [{"id": "some-guid"}]
}
```

Templated endpoints without provided params are skipped. You can combine `--params-file` with `--auto-params-from`; values are merged.

## Analysis & Auto-Params
- Analyze a dump:
  ```powershell
  python -m lcu_dumper --analyze .\lcu_dump\2025...
  ```
  - Writes `active_endpoints_index.json` (successful endpoints only)
  - Writes `params.autogen.json` by mining IDs (e.g., `puuid`, `summonerId`, `id`, `conversationId`) from prior responses
- Use the pruned index and mined params in a subsequent run for higher success rates:
  ```powershell
  python -m lcu_dumper --from-index .\...\active_endpoints_index.json --auto-params-from .\... --per-endpoint-dir
  ```
- Control volume: `--auto-params-limit 20` and `--auto-params-mode cartesian` for broader combinations, or keep defaults for safety.

## Running
Use Python module entry directly:
```powershell
python -m lcu_dumper --help
```

## Safety Notes
- Default read-only. Mutating methods (`POST/PUT/PATCH/DELETE`) are not called unless `--allow-write` is provided.
- TLS verification is disabled by default for the local self-signed LCU certificate. Only use this tool against your own local client that you trust.
- Secrets: Lockfile password is never logged or written; metadata includes only a safe header subset.

## Troubleshooting
- Client not running: Start the Riot/League client; the lockfile exists only while it runs.
- Lockfile not found: Verify the platform-specific paths above.
- TLS/cert errors: Insecure is default; if you force secure mode and see errors, revert or trust the cert explicitly.
- 401/403/404: Some endpoints require specific states (logged in, lobby, champ select, etc.).
- Slow or rate issues: Reduce `--concurrency`, increase `--timeout`, or narrow `--include`.

## Exit Codes
- `0`: All requests succeeded.
- `2`: Partial success (some failures).
- `1`: Fatal error (no lockfile, discovery failed, or all requests failed).

## Project Layout
```text
lcu_dumper/
  __init__.py
  __main__.py   # module entry; python -m lcu_dumper
  cli.py        # CLI wiring, flags, flow
  lockfile.py   # lockfile detection & parsing
  discovery.py  # endpoint discovery
  runner.py     # filtering, param expansion, concurrency, file output
  io_utils.py   # helpers: I/O, colors, patterns
  analyze.py    # dump analyzer, auto-params, pruning
README.md
params.json      # sample; optional
```

## Disclaimer
This project is not affiliated with, endorsed, or sponsored by Riot Games, Inc. Use at your own risk and only against your own local client.

## License
MIT — see LICENSE.
