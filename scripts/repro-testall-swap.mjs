// Repro loop for: "Test all" replaces generated options with FastAPI demo cards.
// Symptom (user): prompt "Add login rate limiting and account lockout to the auth
// module" generates rate-limiter options; clicking "Test all" shows unrelated
// FastAPI bearer-guard / inline-validation / permissive-header demo cards instead.
//
// This harness replicates the EXACT gate logic that decides the swap:
//   - regexes copied verbatim from src/services/daemonSandboxRunner.ts:166,169-175
//   - swap decision copied from src/extension.ts:151-160 (pickFixtureId + gate)
// Run: node scripts/repro-testall-swap.mjs   (exits 1 = bug reproduced)

// --- src/services/daemonSandboxRunner.ts:166 ---
const isPythonMergeImplementation = (code) =>
  /^\s*def\s+merge_intervals\s*\(\s*intervals\s*[\),]/m.test(code);

// --- src/services/daemonSandboxRunner.ts:169-175 ---
const isFastApiAuthImplementation = (code) =>
  /^\s*def\s+create_app\s*\(\s*\)\s*(?:->[^:]+)?:/m.test(code) &&
  /\bFastAPI\b/.test(code) &&
  /\/protected/.test(code);

// --- src/extension.ts:441-449 (pickFixtureId) + 151-160 (swap gate) ---
function decideTestAll(options) {
  const fastapiOk = options.filter((o) => isFastApiAuthImplementation(o.generatedCode)).length >= 2;
  const mergeOk = options.filter((o) => isPythonMergeImplementation(o.generatedCode)).length >= 2;
  const fixtureId = fastapiOk ? "fastapi-auth-endpoint" : mergeOk ? "python-merge" : undefined;
  const swapped = options.length < 2 || !fixtureId;
  return { fixtureId, swapped };
}

// Representative generated output for the rate-limiter prompt (auth module work).
const rateLimiterOptions = [
  {
    title: "Sliding window rate limiter with account lockout",
    generatedCode: `### src/auth/rate_limit.py
\`\`\`python
import time
from collections import defaultdict

_attempts: dict[str, list[float]] = defaultdict(list)
_locked_until: dict[str, float] = {}

def check_login_allowed(username: str, window_s: int = 60, max_attempts: int = 5) -> bool:
    now = time.time()
    if _locked_until.get(username, 0) > now:
        return False
    recent = [t for t in _attempts[username] if now - t < window_s]
    _attempts[username] = recent
    return len(recent) < max_attempts

def record_failed_login(username: str, lockout_s: int = 900) -> None:
    _attempts[username].append(time.time())
    if len(_attempts[username]) >= 5:
        _locked_until[username] = time.time() + lockout_s
\`\`\``,
  },
  {
    title: "Token bucket rate limiter with immediate lockout",
    generatedCode: `### src/auth/rate_limit.py
\`\`\`python
import time

class TokenBucket:
    def __init__(self, capacity: int = 5, refill_per_s: float = 0.1):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_per_s = refill_per_s
        self.updated = time.time()
        self.locked = False

    def allow(self) -> bool:
        if self.locked:
            return False
        now = time.time()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_s)
        self.updated = now
        if self.tokens < 1:
            self.locked = True
            return False
        self.tokens -= 1
        return True
\`\`\``,
  },
];

const result = decideTestAll(rateLimiterOptions);

console.log("fixtureId:", result.fixtureId);
console.log("swapped to FastAPI demo:", result.swapped);

if (result.swapped) {
  console.error(
    "\nBUG REPRODUCED: generated rate-limiter options matched no runnable fixture " +
      "(create_app/FastAPI/protected or merge_intervals), so Test all discards them " +
      "and shows the FastAPI auth demo cards instead."
  );
  process.exit(1);
}
console.log("\nOK: generated options were kept and tested.");
process.exit(0);
