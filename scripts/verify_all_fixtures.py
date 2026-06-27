#!/usr/bin/env python3
"""Verify every mock-shop module fixture end to end.

For each fixtures/mock-shop-*/ : replicate the daemon workspace (copy fixture root
minus candidates/, overlay each candidate at target_file) and run bench_runner.py.
Reports per-candidate verdict and asserts the fixture has teeth:
  - at least 2 candidates PASS (a winner can be chosen)
  - at least 1 candidate FAILS (the deliberately-flawed control)

Modes: local (host python, fast) | docker (build image + run in sandbox).
Usage: python3 scripts/verify_all_fixtures.py [local|docker]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
# Docker shares /Users but not $TMPDIR on macOS; keep workspaces under $HOME.
WORK = Path.home() / ".bench" / "all-fixtures-verify"
PY = sys.executable


def fixture_dirs() -> list[Path]:
    return sorted(p for p in FIXTURES.glob("mock-shop-*") if (p / "bench.json").is_file())


def run_candidate(fix: Path, cfg: dict, candidate: Path, mode: str) -> dict:
    target = cfg["target_file"]
    ws = WORK / fix.name / candidate.stem
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    # copy fixture root except the candidates dir + pycache
    shutil.copytree(
        fix, ws, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(cfg.get("candidates_dir", "candidates"), "__pycache__", "*.pyc"),
    )
    dest = ws / target
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(candidate.read_text())

    if mode == "docker":
        cmd = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{ws}:/work:ro", "-w", "/work", cfg["docker_image"],
            "sh", "-lc", cfg["runner"],
        ]
    else:
        cmd = [PY, "bench_runner.py"]
    proc = subprocess.run(cmd, cwd=None if mode == "docker" else ws,
                          capture_output=True, text=True)
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        data = json.loads(last)
        return {"passed": data["tests"]["passed"], "failed": data["tests"]["failed"],
                "total": data["tests"]["total"]}
    except Exception:
        return {"error": (proc.stdout + proc.stderr)[-400:]}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"
    rc = 0
    for fix in fixture_dirs():
        cfg = json.loads((fix / "bench.json").read_text())
        if mode == "docker":
            print(f"building {cfg['docker_image']} ...")
            b = subprocess.run(["docker", "build", "-q", "-f", str(fix / cfg["dockerfile"]),
                                "-t", cfg["docker_image"], str(fix / cfg["docker_context"])],
                               capture_output=True, text=True)
            if b.returncode != 0:
                print(f"  IMAGE BUILD FAILED: {b.stderr[-300:]}"); rc = 1; continue
        cands = sorted((fix / cfg.get("candidates_dir", "candidates")).glob("*.py"))
        print(f"\n== {cfg['id']}  (target {cfg['target_file']}, {len(cands)} candidates)")
        passes = fails = 0
        for c in cands:
            r = run_candidate(fix, cfg, c, mode)
            if "error" in r:
                print(f"   {c.stem:24} ERROR  {r['error'][:120]!r}"); fails += 1; rc = 1; continue
            verdict = "pass" if r["failed"] == 0 else "fail"
            print(f"   {c.stem:24} {verdict:4}  {r['passed']}/{r['total']}")
            passes += verdict == "pass"
            fails += verdict == "fail"
        if passes < 2:
            print(f"   !! only {passes} passing candidates (need >=2 to pick a winner)"); rc = 1
        if fails < 1:
            print(f"   !! no failing candidate (fixture may lack teeth)"); rc = 1
    print("\n" + ("ALL FIXTURES OK" if rc == 0 else "FIXTURE VERIFY FAILED"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
