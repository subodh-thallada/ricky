#!/usr/bin/env node
// Single source of truth for the mock-shop module fixtures.
//
// Each mock-shop module (auth, checkout, payments, db, models) is an independent
// Docker fixture under fixtures/mock-shop-<module>/ that targets one file in the
// mock_shop package. The shared bits — the mock_shop package itself, the
// Dockerfile, and bench_runner.py — must be byte-identical across every fixture so
// a candidate for module X runs against the same baseline siblings. This script:
//
//   1. SYNC: copies the canonical package + Dockerfile + bench_runner.py from the
//      auth fixture into every other mock-shop-* fixture (idempotent).
//   2. CODEGEN: reads each fixture's bench.json `routing` block + its candidate
//      files and emits src/fixtures/mockShopReference.generated.ts — the registry
//      the VS Code extension uses to detect which module a candidate targets, route
//      it to the right fixture, and supply per-module reference candidates when a
//      "Test all" run has nothing runnable (no more auth bait-and-switch).
//
// Run after editing any fixture:  node scripts/sync-mock-shop-fixtures.mjs
// Add --check to fail (exit 1) if anything is out of sync / stale (for CI).

import { readFileSync, writeFileSync, readdirSync, statSync, mkdirSync, cpSync, rmSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURES = join(ROOT, "fixtures");
const CANON = join(FIXTURES, "mock-shop-auth"); // template: canonical package + runner + Dockerfile
const SHARED = ["mock_shop", "Dockerfile", "bench_runner.py"];
const GENERATED_TS = join(ROOT, "src", "fixtures", "mockShopReference.generated.ts");

const CHECK = process.argv.includes("--check");

function listMockShopFixtures() {
  return readdirSync(FIXTURES)
    .filter((name) => name.startsWith("mock-shop-"))
    .map((name) => join(FIXTURES, name))
    .filter((dir) => statSync(dir).isDirectory())
    .sort();
}

// ---- 1. SYNC shared files from the canonical fixture into every sibling --------

function readShared(dir, rel) {
  // Read a shared path as a flat {relativePath: contents} map for comparison/copy.
  const abs = join(dir, rel);
  const out = {};
  const walk = (p, base) => {
    const st = statSync(p);
    if (st.isDirectory()) {
      for (const child of readdirSync(p)) {
        if (child === "__pycache__" || child.endsWith(".pyc")) continue;
        walk(join(p, child), join(base, child));
      }
    } else {
      out[base] = readFileSync(p, "utf8");
    }
  };
  walk(abs, rel);
  return out;
}

function syncShared() {
  const drift = [];
  const canon = Object.fromEntries(SHARED.map((rel) => [rel, readShared(CANON, rel)]));
  for (const dir of listMockShopFixtures()) {
    if (dir === CANON) continue;
    for (const rel of SHARED) {
      const want = canon[rel];
      let have = {};
      try {
        have = readShared(dir, rel);
      } catch {
        have = {};
      }
      const same =
        Object.keys(want).length === Object.keys(have).length &&
        Object.entries(want).every(([k, v]) => have[k] === v);
      if (same) continue;
      drift.push(`${basename(dir)}/${rel}`);
      if (!CHECK) {
        const dst = join(dir, rel);
        rmSync(dst, { recursive: true, force: true });
        cpSync(join(CANON, rel), dst, { recursive: true });
      }
    }
    // Never let a stale __pycache__ ride along.
    rmSync(join(dir, "mock_shop", "__pycache__"), { recursive: true, force: true });
  }
  return drift;
}

// ---- 2. CODEGEN the TS registry from each fixture's bench.json + candidates -----

function titleize(stem) {
  return stem.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fenceCandidate(basenamePy, source) {
  const body = source.replace(/\s+$/, "");
  return `### mock_shop/${basenamePy}\n\n\`\`\`python\n${body}\n\`\`\`\n`;
}

function buildModule(dir) {
  const bench = JSON.parse(readFileSync(join(dir, "bench.json"), "utf8"));
  const routing = bench.routing;
  if (!routing) throw new Error(`${basename(dir)}/bench.json is missing a "routing" block`);
  const { module, basename: basenamePy, required_defs, reference_candidates, prompt_hint } = routing;
  const refs = new Set(reference_candidates ?? []);

  const candDir = join(dir, bench.candidates_dir ?? "candidates");
  const candidateFiles = readdirSync(candDir)
    .filter((f) => f.endsWith(".py"))
    .sort();

  const candidates = candidateFiles.map((file) => {
    const stem = file.replace(/\.py$/, "");
    const source = readFileSync(join(candDir, file), "utf8");
    return {
      id: stem,
      title: titleize(stem),
      summary: `${bench.label} — ${titleize(stem)} approach.`,
      tradeoffs: [`Reference ${module} candidate (${stem}).`],
      generatedCode: fenceCandidate(basenamePy, source),
      expectPass: refs.has(stem),
    };
  });
  // References first (the known-good ones), flawed control last.
  candidates.sort((a, b) => Number(b.expectPass) - Number(a.expectPass) || a.id.localeCompare(b.id));

  return {
    fixtureId: bench.id,
    module,
    targetFile: bench.target_file,
    basename: basenamePy,
    label: bench.label,
    requiredDefs: required_defs,
    promptHint: prompt_hint ?? "",
    candidates,
  };
}

function renderTs(modules) {
  const header = `// AUTO-GENERATED by scripts/sync-mock-shop-fixtures.mjs — do not edit by hand.
// Source of truth: fixtures/mock-shop-*/bench.json (routing block) + candidates/*.py.
// Regenerate after editing any mock-shop fixture: node scripts/sync-mock-shop-fixtures.mjs

export type MockShopReferenceCandidate = {
  id: string;
  title: string;
  summary: string;
  tradeoffs: string[];
  generatedCode: string;
  expectPass: boolean;
};

export type MockShopModule = {
  fixtureId: string;
  module: string;
  targetFile: string;
  basename: string;
  label: string;
  requiredDefs: string[];
  promptHint: string;
  candidates: MockShopReferenceCandidate[];
};

export const MOCK_SHOP_MODULES: MockShopModule[] = ${JSON.stringify(modules, null, 2)};

export const MOCK_SHOP_FIXTURE_IDS: string[] = MOCK_SHOP_MODULES.map((m) => m.fixtureId);
`;
  return header;
}

// ---- run ----------------------------------------------------------------------

const driftedShared = syncShared();
const modules = listMockShopFixtures().map(buildModule);
// Stable, README-aligned order so routing prefers the real-feature modules first.
const ORDER = ["auth", "checkout", "payments", "db", "models"];
modules.sort((a, b) => {
  const ai = ORDER.indexOf(a.module);
  const bi = ORDER.indexOf(b.module);
  return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.fixtureId.localeCompare(b.fixtureId);
});

const ts = renderTs(modules);
let tsStale = false;
try {
  tsStale = readFileSync(GENERATED_TS, "utf8") !== ts;
} catch {
  tsStale = true;
}

if (CHECK) {
  const problems = [];
  if (driftedShared.length) problems.push(`shared files out of sync: ${driftedShared.join(", ")}`);
  if (tsStale) problems.push(`${GENERATED_TS} is stale`);
  if (problems.length) {
    console.error("sync-mock-shop-fixtures --check FAILED:\n  " + problems.join("\n  "));
    process.exit(1);
  }
  console.log(`OK — ${modules.length} fixtures in sync, registry current.`);
  process.exit(0);
}

if (tsStale) {
  mkdirSync(dirname(GENERATED_TS), { recursive: true });
  writeFileSync(GENERATED_TS, ts);
}

console.log(
  `synced ${modules.length} mock-shop fixtures` +
    (driftedShared.length ? ` (rewrote ${driftedShared.length} shared files)` : "") +
    `; ${tsStale ? "regenerated" : "registry already current:"} ${GENERATED_TS.replace(ROOT + "/", "")}`
);
for (const m of modules) {
  const refs = m.candidates.filter((c) => c.expectPass).map((c) => c.id);
  const flawed = m.candidates.filter((c) => !c.expectPass).map((c) => c.id);
  console.log(`  ${m.fixtureId.padEnd(20)} target=${m.targetFile.padEnd(20)} refs=[${refs}] flawed=[${flawed}]`);
}
