#!/usr/bin/env node
/**
 * Ejecuta `tauri build` y vuelca stdout/stderr (incl. build-script / cargo) a la consola.
 */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DESKTOP = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const npx = process.platform === "win32" ? "npx.cmd" : "npx";

const result = spawnSync(npx, ["tauri", "build"], {
  cwd: DESKTOP,
  encoding: "utf8",
  shell: process.platform === "win32",
  env: { ...process.env, RUST_BACKTRACE: process.env.RUST_BACKTRACE || "1" },
  maxBuffer: 64 * 1024 * 1024,
});

if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);

if (result.error) {
  console.error("");
  console.error("ERROR: no se pudo ejecutar tauri build:", result.error.message);
  process.exit(1);
}

if (result.status !== 0) {
  console.error("");
  console.error(
    "--- tauri build falló (código " +
      result.status +
      "). Revise la salida de cargo/tauri-build arriba (stderr). ---"
  );
  console.error(
    "Causas frecuentes: falta sidecar backend-*.exe, iconos, o tauri.conf.json inválido."
  );
  process.exit(result.status ?? 1);
}
