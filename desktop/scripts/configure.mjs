#!/usr/bin/env node
// ============================================================
// configure.mjs — Genera los archivos variables del launcher Tauri
// a partir de desktop/smartsuite.config.json:
//   - src-tauri/tauri.conf.json   (nombre, id, iconos, ventana, descripciones)
//   - src-tauri/appconfig.json    (dataDirName + modelos de Ollama para Rust)
//   - ui/ccce-theme.css + ui/accent.css  (marca CCCE + acento por-herramienta)
//
// Se ejecuta automáticamente antes de 'npm run build' / 'npm run dev'.
// ============================================================
import { readFileSync, writeFileSync, copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP = resolve(HERE, "..");
const cfgPath = resolve(DESKTOP, "smartsuite.config.json");
const cfg = JSON.parse(readFileSync(cfgPath, "utf8"));

function req(name) {
  if (!cfg[name]) throw new Error(`smartsuite.config.json: falta '${name}'`);
  return cfg[name];
}

// --- 1) tauri.conf.json ---
const tauriConf = {
  $schema: "https://schema.tauri.app/config/2",
  productName: req("productName"),
  version: cfg.version || "1.0.0",
  identifier: req("identifier"),
  build: { frontendDist: "../ui" },
  app: {
    withGlobalTauri: true,
    windows: [
      {
        label: "main",
        title: (cfg.window && cfg.window.title) || cfg.productName,
        width: (cfg.window && cfg.window.width) || 1200,
        height: (cfg.window && cfg.window.height) || 800,
        minWidth: (cfg.window && cfg.window.minWidth) || 900,
        minHeight: (cfg.window && cfg.window.minHeight) || 600,
        resizable: true,
        center: true,
      },
    ],
    security: { csp: null },
  },
  bundle: {
    active: true,
    targets: "all",
    icon: [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico",
    ],
    externalBin: ["binaries/backend"],
    category: "Finance",
    shortDescription: cfg.shortDescription || cfg.productName,
    longDescription: cfg.longDescription || cfg.productName,
  },
  plugins: {},
};
writeFileSync(
  resolve(DESKTOP, "src-tauri/tauri.conf.json"),
  JSON.stringify(tauriConf, null, 2) + "\n"
);

// --- 2) appconfig.json (lo lee Rust vía include_str!) ---
const tiers = ((cfg.ollama && cfg.ollama.tiers) || [{ maxRamGb: 0, model: "llama3.2:3b" }]).map(
  (t) => ({ maxRamGb: Number(t.maxRamGb) || 0, model: String(t.model) })
);
const extraModels = ((cfg.ollama && cfg.ollama.extraModels) || []).map(String);
const appConfig = { dataDirName: req("dataDirName"), ollamaTiers: tiers, extraModels };
writeFileSync(
  resolve(DESKTOP, "src-tauri/appconfig.json"),
  JSON.stringify(appConfig, null, 2) + "\n"
);

// --- 3) Marca CCCE + acento por-herramienta en la UI de carga ---
mkdirSync(resolve(DESKTOP, "ui"), { recursive: true });
copyFileSync(resolve(DESKTOP, "brand/ccce-theme.css"), resolve(DESKTOP, "ui/ccce-theme.css"));
const accent = cfg.accent || "#F4C10E";
writeFileSync(
  resolve(DESKTOP, "ui/accent.css"),
  `/* Generado por configure.mjs — acento por-herramienta */\n:root { --ccce-accent: ${accent}; }\n`
);

console.log(`configure.mjs: '${cfg.productName}' configurado (accent ${accent}, dataDir ${cfg.dataDirName}).`);
