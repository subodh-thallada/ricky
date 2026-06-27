from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = PROJECT_ROOT / "fixtures"
# Default under $HOME, not /tmp: Docker Desktop on macOS shares /Users by default but
# not /tmp (/tmp -> /private/tmp), so a /tmp workspace bind-mounts EMPTY into the
# container and the runner can't find its files. Override with BENCH_RUNS_ROOT.
LOCAL_RUNS_ROOT = Path(
    os.environ.get("BENCH_RUNS_ROOT", str(Path.home() / ".bench" / "runs"))
)
