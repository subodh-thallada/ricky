// Verifies the extension-side wiring for the mock-shop MODULE fixtures (auth /
// checkout / payments / db / models). This mirrors the routing logic in
// src/services/daemonSandboxRunner.ts and drives it from the on-disk fixtures
// (each fixtures/mock-shop-*/bench.json `routing` block + a reference candidate),
// asserting:
//   1. generatedCode -> candidate files extraction lands the code at target_file
//      (raw, fenced, "### path"-headed forms).
//   2. each module's reference candidate matches ITS OWN requiredDefs and NO other
//      module's (requiredDefs are disjoint -> deterministic routing).
//   3. pickFixtureId routes two same-module options to that module's fixture (the
//      bug this guards against: a checkout candidate being swapped to auth).
//   4. a non-mock-shop snippet routes to no mock-shop fixture.
//   5. the EXTRACTED file is actually runnable: drop it into the package and run
//      bench_runner.py -> all tests pass.
//
// Run: node scripts/verify-extension-wiring.mjs   (exit 1 = wiring broken)
import { execFileSync } from "node:child_process";
import { mkdtempSync, cpSync, writeFileSync, rmSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURES = join(ROOT, "fixtures");
const MOCK_SHOP_PACKAGE = "mock_shop";

// ---- load module fixtures straight from disk (bench.json routing = source of truth)
const MODULES = readdirSync(FIXTURES)
  .filter((name) => name.startsWith("mock-shop-"))
  .map((name) => {
    const dir = join(FIXTURES, name);
    const bench = JSON.parse(readFileSync(join(dir, "bench.json"), "utf8"));
    const r = bench.routing;
    return {
      dir,
      fixtureId: bench.id,
      targetFile: bench.target_file,
      basename: r.basename,
      requiredDefs: r.required_defs.map((src) => new RegExp(src)),
      referenceCandidates: r.reference_candidates,
      candidatesDir: bench.candidates_dir ?? "candidates"
    };
  })
  .sort((a, b) => a.fixtureId.localeCompare(b.fixtureId));
const FIXTURE_IDS = new Set(MODULES.map((m) => m.fixtureId));

// ---- mirror of daemonSandboxRunner.ts extraction + matching ----
const normalizeCode = (code) => `${code.replace(/\s+$/, "")}\n`;
function parseFileSections(text) {
  const sections = [];
  const headed = /(?:^|\n)###\s*([^\n`]+?)\s*\n+```[\w-]*\n([\s\S]*?)```/g;
  let m;
  while ((m = headed.exec(text)) !== null) sections.push({ path: m[1].trim(), code: normalizeCode(m[2]) });
  if (sections.length) return sections;
  const bare = /```[\w-]*\n([\s\S]*?)```/g;
  while ((m = bare.exec(text)) !== null) sections.push({ path: "", code: normalizeCode(m[1]) });
  return sections;
}
function stripCodeFences(text) {
  const f = /^```[\w-]*\n([\s\S]*?)```\s*$/m.exec(text.trim());
  return f ? normalizeCode(f[1]) : text;
}
function buildCandidateFiles(fixtureId, targetFile, generatedCode) {
  if (!FIXTURE_IDS.has(fixtureId)) return { [targetFile]: generatedCode };
  const files = {};
  const sections = parseFileSections(generatedCode);
  for (const s of sections) {
    const base = s.path.split("/").pop() ?? "";
    if (!base.endsWith(".py")) continue;
    files[`${MOCK_SHOP_PACKAGE}/${base}`] = s.code;
  }
  if (!files[targetFile]) {
    const flat = sections.length ? sections.map((s) => s.code).join("\n\n") : stripCodeFences(generatedCode);
    files[targetFile] = flat.trim() ? flat : generatedCode;
  }
  return files;
}
function matchesMockShopModule(code, mod) {
  const target = buildCandidateFiles(mod.fixtureId, mod.targetFile, code)[mod.targetFile] ?? "";
  return mod.requiredDefs.every((re) => re.test(target));
}
function pickFixtureId(options) {
  for (const mod of MODULES) {
    if (options.filter((o) => matchesMockShopModule(o.generatedCode, mod)).length >= 2) return mod.fixtureId;
  }
  return undefined;
}

// ---- helpers ----
const fence = (basename, source) => `Here is my approach.\n\n### mock_shop/${basename}\n\`\`\`python\n${source}\`\`\`\n`;

let rc = 0;
const check = (name, cond) => { console.log(`${cond ? "OK  " : "FAIL"} ${name}`); if (!cond) rc = 1; };

// 1-4: per-module extraction + routing + disjointness
for (const mod of MODULES) {
  const ref = mod.referenceCandidates[0];
  const source = readFileSync(join(mod.dir, mod.candidatesDir, `${ref}.py`), "utf8");
  const raw = source; // bare body, no fences/headers
  const fenced = fence(mod.basename, source); // "### mock_shop/<file>" + fenced

  // 1. extraction lands at target_file for both shapes
  const filesRaw = buildCandidateFiles(mod.fixtureId, mod.targetFile, raw);
  const filesFenced = buildCandidateFiles(mod.fixtureId, mod.targetFile, fenced);
  check(`[${mod.fixtureId}] raw -> ${mod.targetFile} present`, !!filesRaw[mod.targetFile]);
  check(`[${mod.fixtureId}] fenced -> ${mod.targetFile} present (fence stripped)`,
    !!filesFenced[mod.targetFile] && !filesFenced[mod.targetFile].includes("```"));

  // 2. matches OWN requiredDefs, and NO other module's (disjoint => deterministic)
  check(`[${mod.fixtureId}] reference matches own requiredDefs`, matchesMockShopModule(fenced, mod));
  const collides = MODULES.filter((other) => other.fixtureId !== mod.fixtureId && matchesMockShopModule(fenced, other));
  check(`[${mod.fixtureId}] reference matches no other module`, collides.length === 0,
    collides.length ? `  collides with: ${collides.map((c) => c.fixtureId).join(", ")}` : "");

  // 3. two same-module options route to this fixture (no swap/bait-and-switch)
  const picked = pickFixtureId([{ generatedCode: fenced }, { generatedCode: raw }]);
  check(`[${mod.fixtureId}] pickFixtureId routes two ${mod.basename} options here (got ${picked})`, picked === mod.fixtureId);
}

// 4. a non-mock-shop snippet routes nowhere
const merge = "```python\ndef merge_intervals(intervals):\n    return sorted(intervals)\n```";
check("non-mock-shop snippet routes to no mock-shop fixture",
  pickFixtureId([{ generatedCode: merge }, { generatedCode: merge }]) === undefined);

// 5. the extracted file is runnable against the real package (auth = end-to-end smoke)
const auth = MODULES.find((m) => m.fixtureId === "mock-shop-auth");
if (auth) {
  const source = readFileSync(join(auth.dir, auth.candidatesDir, `${auth.referenceCandidates[0]}.py`), "utf8");
  const extracted = buildCandidateFiles(auth.fixtureId, auth.targetFile, fence(auth.basename, source))[auth.targetFile];
  const ws = mkdtempSync(join(tmpdir(), "ext-wiring-"));
  try {
    cpSync(join(auth.dir, "mock_shop"), join(ws, "mock_shop"), { recursive: true });
    cpSync(join(auth.dir, "test_candidate.py"), join(ws, "test_candidate.py"));
    cpSync(join(auth.dir, "bench_runner.py"), join(ws, "bench_runner.py"));
    rmSync(join(ws, "mock_shop", "__pycache__"), { recursive: true, force: true });
    writeFileSync(join(ws, auth.targetFile), extracted);
    const out = execFileSync(process.env.PYTHON || "python3", ["bench_runner.py"], { cwd: ws, encoding: "utf-8" });
    const json = JSON.parse(out.trim().split("\n").pop());
    check(`[mock-shop-auth] extracted file passes all tests (${json.tests.passed}/${json.tests.total})`,
      json.tests.failed === 0 && json.tests.total >= 6);
  } catch (e) {
    check("[mock-shop-auth] extracted file runs", false);
    console.error(e.stdout || e.message);
  } finally {
    rmSync(ws, { recursive: true, force: true });
  }
}

console.log(rc === 0 ? `\nWIRING OK (${MODULES.length} module fixtures)` : "\nWIRING FAILED");
process.exit(rc);
