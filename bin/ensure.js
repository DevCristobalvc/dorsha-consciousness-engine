#!/usr/bin/env node
/**
 * postinstall: ensure the engine is reachable (warn only — resolution happens
 * at runtime in bin/ce.js so `npx` works without a pre-installed Python).
 */
const { execFileSync } = require("node:child_process");

for (const [cmd, args] of [
  ["uvx", ["--version"]],
  ["pipx", ["--version"]],
  ["ce", ["--help"]],
]) {
  try {
    execFileSync(cmd, args, { stdio: "ignore" });
    process.exit(0);
  } catch {
    /* continue */
  }
}
console.warn(
  "ce: no runtime found (uv, pipx or a pre-installed ce). Install with:\n" +
    "  pip install dorsha-consciousness-engine\n" +
    "or add the standalone binary to dist/ce-<platform>.",
);
