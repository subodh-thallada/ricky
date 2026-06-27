from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "failed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench-daemon")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Start the local FastAPI daemon")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    run = subcommands.add_parser("run", help="POST /runs and print the Decision Payload")
    run.add_argument("--base-url", default="http://127.0.0.1:8000")
    run.add_argument("--fixture-id", default="python-merge")
    run.add_argument("--rebuild-image", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=0.25)
    run.add_argument(
        "--request-json",
        help="Path to a Candidate Evaluation Request JSON file, or '-' for stdin.",
    )

    fixtures = subcommands.add_parser("fixtures", help="List daemon fixtures")
    fixtures.add_argument("--base-url", default="http://127.0.0.1:8000")

    args = parser.parse_args(argv)

    if args.command == "serve":
        return _serve(args.host, args.port, args.reload)
    if args.command == "run":
        return _run(
            args.base_url,
            args.fixture_id,
            args.rebuild_image,
            args.poll_seconds,
            args.request_json,
        )
    if args.command == "fixtures":
        payload = _request_json("GET", f"{args.base_url}/fixtures")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    parser.error("unknown command")
    return 2


def _serve(host: str, port: int, reload: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed. Install dependencies with: "
            "python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    uvicorn.run("bench_daemon.app:app", host=host, port=port, reload=reload)
    return 0


def _run(
    base_url: str,
    fixture_id: str,
    rebuild_image: bool,
    poll_seconds: float,
    request_json: str | None = None,
) -> int:
    body = _load_run_request(request_json) if request_json else {
        "fixture_id": fixture_id,
        "rebuild_image": rebuild_image,
        "candidates": None,
    }
    created = _request_json("POST", f"{base_url}/runs", body)
    run_id = created["run_id"]

    while True:
        payload = _request_json("GET", f"{base_url}/runs/{run_id}")
        if payload.get("status") in TERMINAL_STATUSES:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if payload.get("status") == "completed" else 1
        time.sleep(poll_seconds)


def _load_run_request(path: str | None) -> dict[str, Any]:
    if not path:
        raise SystemExit("--request-json requires a file path or '-'")

    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid request JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise SystemExit("Candidate Evaluation Request JSON must be an object")
    return payload


def _request_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} failed with HTTP {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
