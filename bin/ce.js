#!/usr/bin/env node
/**
 * ce — Dorsha Consciousness Engine CLI shim.
 *
 * Resolves the engine binary in order:
 *   1. CE_BIN env var (explicit path)
 *   2. standalone binary bundled for this platform (dist/ce-<platform>) — future
 *   3. `ce` already on PATH (pip install -e . / pipx / uv tool)
 *   4. `uvx dorsha-ce` (no install — uv downloads the PyPI package)
 *   5. `pipx run dorsha-ce`
 *
 * Usage: ce mcp | ce panel | ce recall "..." | ...
 */
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

function findCe() {
  const explicit = process.env.CE_BIN;
  if (explicit && fs.existsSync(explicit)) return [explicit, []];

  const platform = `${process.platform}-${process.arch}`;
  const standalone = path.join(__dirname, "..", "dist", `ce-${platform}`);
  if (fs.existsSync(standalone)) return [standalone, []];

  return null;
}

function runWith(args) {
  const [cmd, preArgs] = args;
  const child = spawn(cmd, [...preArgs, ...process.argv.slice(2)], {
    stdio: "inherit",
    env: process.env,
  });
  child.on("exit", (code) => process.exit(code ?? 1));
}

async function main() {
  const found = findCe();
  if (found) return runWith(found);

  // no local binary → try uvx / pipx (python ≥3.11 required, uv preferred)
  const { execFileSync } = require("node:child_process");
  for (const [cmd, pre] of [
    ["uvx", ["dorsha-ce"]],
    ["pipx", ["run", "dorsha-ce"]],
  ]) {
    try {
      execFileSync(cmd, ["--version"], { stdio: "ignore" });
      return runWith([cmd, pre]);
    } catch {
      /* try next */
    }
  }
  console.error(
    "ce: engine not found. Install with: pip install dorsha-consciousness-engine\n" +
      "or set CE_BIN to the ce executable path.",
  );
  process.exit(1);
}

main();
