from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = PROJECT_ROOT / "fixtures"
LOCAL_RUNS_ROOT = Path(os.environ.get("BENCH_RUNS_ROOT", "/tmp/bench/runs"))
