#!/usr/bin/env bash
# Feedback loop for EVERY mock-shop module fixture (auth / checkout / payments / db
# / models). Replicates the daemon's workspace creation — copy the fixture root,
# overlay each candidate as the fixture's target_file — runs bench_runner.py, and
# asserts the expected verdict per candidate. Expected verdicts are DERIVED from
# each bench.json `routing.reference_candidates` (those must PASS; every other
# candidate in candidates/ is the flawed control and must FAIL), so there is nothing
# hardcoded here — add a fixture and it is covered automatically.
#
# Modes:
#   local  (default) : run bench_runner.py with host python (fast, ~1s/fixture)
#   docker           : build each fixture image and run in the sandbox container
#
# Usage: bash scripts/verify-mock-shop-fixtures.sh [local|docker]
set -uo pipefail

MODE="${1:-local}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
# Under $HOME, not $TMPDIR: Docker Desktop on macOS shares /Users but not
# /var/folders, so a $TMPDIR workspace bind-mounts EMPTY (same caveat as paths.py).
WORK="${BENCH_VERIFY_ROOT:-$HOME/.bench/ms-fixtures-verify}"

jget() { # jget <bench.json> <python-expr over `d`>
  "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"
}

rc=0
fixtures_seen=0
for FIX in "$ROOT"/fixtures/mock-shop-*; do
  [ -f "$FIX/bench.json" ] || continue
  fixtures_seen=$((fixtures_seen + 1))
  fid="$(jget "$FIX/bench.json" 'd["id"]')"
  target="$(jget "$FIX/bench.json" 'd["target_file"]')"
  cdir="$(jget "$FIX/bench.json" 'd.get("candidates_dir","candidates")')"
  image="$(jget "$FIX/bench.json" 'd["docker_image"]')"
  runner="$(jget "$FIX/bench.json" 'd["runner"]')"
  refs="$(jget "$FIX/bench.json" '" ".join(d["routing"]["reference_candidates"])')"

  echo "== $fid  (target $target) =="
  if [ "$MODE" = "docker" ]; then
    docker build -q -f "$FIX/Dockerfile" -t "$image" "$FIX" >/dev/null || { echo "  image build failed"; rc=1; continue; }
  fi

  for cand in "$FIX/$cdir"/*.py; do
    stem="$(basename "$cand" .py)"
    want=fail
    for r in $refs; do [ "$r" = "$stem" ] && want=pass; done

    ws="$WORK/$fid/$stem"
    rm -rf "$ws"; mkdir -p "$ws"
    cp -R "$FIX/mock_shop" "$ws/mock_shop"
    cp "$FIX/test_candidate.py" "$FIX/bench_runner.py" "$ws/"
    cp "$cand" "$ws/$target"
    rm -rf "$ws/mock_shop/__pycache__"

    if [ "$MODE" = "docker" ]; then
      out="$(docker run --rm --network none --memory 256m --pids-limit 128 -v "$ws:/work:ro" -w /work "$image" sh -lc "$runner" 2>&1)"
    else
      out="$(cd "$ws" && "$PY" bench_runner.py 2>&1)"
    fi

    json="$(printf '%s\n' "$out" | tail -n 1)"
    summary="$("$PY" -c '
import json,sys
d=json.loads(sys.argv[1]); t=d["tests"]; m=d.get("metrics",{})
print(t["passed"], t["failed"], t["total"], m.get("feature_tests","?"), m.get("regression_tests","?"))
' "$json" 2>/dev/null)"
    if [ -z "$summary" ]; then
      echo "  [$stem] ERROR: runner produced no JSON verdict"
      printf '%s\n' "$out" | tail -n 12
      rc=1; continue
    fi
    set -- $summary; passed=$1; failed=$2; total=$3; feat=$4; regr=$5
    got=$([ "$failed" -eq 0 ] && echo pass || echo fail)
    if [ "$got" = "$want" ]; then mark=OK; else mark=MISMATCH; rc=1; fi
    printf '  [%-20s] passed=%s failed=%s total=%s (feat=%s reg=%s) -> %s (want %s) %s\n' \
      "$stem" "$passed" "$failed" "$total" "$feat" "$regr" "$got" "$want" "$mark"
  done
done

echo
if [ "$fixtures_seen" -eq 0 ]; then
  echo "NO mock-shop fixtures found under $ROOT/fixtures/"; exit 2
fi
if [ $rc -eq 0 ]; then
  echo "ALL MOCK-SHOP FIXTURES ($fixtures_seen): EXPECTED VERDICTS MATCHED  [$MODE]"
else
  echo "MOCK-SHOP FIXTURE VERIFY FAILED  [$MODE]"
fi
exit $rc
