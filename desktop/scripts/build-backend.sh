#!/usr/bin/env bash
# ============================================================
# build-backend.sh — Empaqueta el backend FastAPI de SmartCaja
# en un binario único (sidecar de Tauri) con PyInstaller y lo
# coloca en desktop/src-tauri/binaries con el sufijo del target.
# ============================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DESKTOP="$(dirname "$HERE")"
ROOT="$(dirname "$DESKTOP")"
cd "$ROOT"

# --- Verificar Rust (se usa para el target triple y luego para 'npm run build') ---
if ! command -v rustc >/dev/null 2>&1; then
  echo "ERROR: no se encontró 'rustc' (Rust) en el PATH." >&2
  echo "       Instálalo con rustup (https://rustup.rs) y reabre la terminal:" >&2
  echo "         curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh" >&2
  echo "       Rust también es necesario para 'npm run build'." >&2
  exit 1
fi

# --- Seleccionar intérprete de Python (preferir 3.12; soportado 3.10–3.12) ---
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PY="python3.12"
else
  PY="python3"
fi

VER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
MAJOR="${VER%%.*}"; MINOR="${VER##*.}"
if [ "$MAJOR" != "3" ] || [ "$MINOR" -lt 10 ] || [ "$MINOR" -gt 12 ]; then
  echo "ERROR: Python $VER no es compatible para empaquetar el backend." >&2
  echo "       numpy 1.26.4 (pinneado) solo tiene wheels para Python 3.10-3.12;" >&2
  echo "       con 3.13/3.14 pip compila desde fuente y falla." >&2
  echo "       Crea el venv con Python 3.12:  python3.12 -m venv venv" >&2
  exit 1
fi
echo "==> Usando Python $VER ($PY)"

# Usa requirements-desktop.txt (subconjunto liviano, sin deps pesadas opcionales
# como torch/easyocr) si existe; si no, cae a requirements.txt.
REQ="requirements.txt"
[ -f "requirements-desktop.txt" ] && REQ="requirements-desktop.txt"
echo "==> Instalando dependencias de build desde $REQ (+ PyInstaller)"
"$PY" -m pip install --quiet -r "$REQ" pyinstaller

echo "==> Empaquetando backend con PyInstaller"
"$PY" -m PyInstaller --clean --noconfirm \
  --distpath desktop/backend/dist --workpath desktop/backend/build \
  desktop/backend/backend.spec

TRIPLE="$(rustc -vV | sed -n 's/host: //p')"
mkdir -p desktop/src-tauri/binaries
cp "desktop/backend/dist/backend" "desktop/src-tauri/binaries/backend-${TRIPLE}"
chmod +x "desktop/src-tauri/binaries/backend-${TRIPLE}"
echo "==> Sidecar listo: desktop/src-tauri/binaries/backend-${TRIPLE}"
