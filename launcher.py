"""
launcher.py — Lanzador cross-platform para SmartGastos.
Ejecutado por start.json. Detecta la plataforma y usa la ruta
correcta del Python del venv para iniciar server/app.py.

Funciona en: macOS, Windows, Linux.

COMPATIBILIDAD WINDOWS:
  - Usa venv/Scripts/python.exe (no venv/bin/python)
  - Agrega tools/poppler, Tesseract y Ollama al PATH
  - Usa 127.0.0.1 (no localhost)
  - No usa exec, fork ni senales Unix
  - Encoding UTF-8 forzado
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
IS_WINDOWS = sys.platform == "win32"

# Ruta al Python del venv segun plataforma
if IS_WINDOWS:
    VENV_PYTHON = BASE_DIR / "venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"

SERVER_SCRIPT = BASE_DIR / "server" / "app.py"


def log(msg: str):
    print(f"[launcher] {msg}", flush=True)


def setup_windows_path(env: dict) -> dict:
    """Agrega herramientas al PATH en Windows."""
    additions = []

    # Poppler portable
    tools_dir = BASE_DIR / "tools"
    if tools_dir.exists():
        for poppler_bin in tools_dir.rglob("pdftoppm.exe"):
            additions.append(str(poppler_bin.parent))
            break

    # Tesseract en rutas comunes
    if not shutil.which("tesseract"):
        tess_candidates = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tesseract-OCR",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR",
        ]
        for tess_dir in tess_candidates:
            try:
                if (tess_dir / "tesseract.exe").exists():
                    additions.append(str(tess_dir))
                    break
            except (OSError, ValueError):
                continue

    # Ollama en rutas comunes
    if not shutil.which("ollama"):
        ollama_candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Ollama",
            Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Programs" / "Ollama",
        ]
        for ollama_dir in ollama_candidates:
            try:
                if (ollama_dir / "ollama.exe").exists():
                    additions.append(str(ollama_dir))
                    break
            except (OSError, ValueError):
                continue

    if additions:
        current_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(additions) + os.pathsep + current_path
        log(f"PATH extendido con: {additions}")

    return env


def main():
    if not VENV_PYTHON.exists():
        log(f"ERROR: Venv no encontrado en {VENV_PYTHON}")
        log("Por favor reinstala el plugin desde Pinokio.")
        sys.exit(1)

    if not SERVER_SCRIPT.exists():
        log(f"ERROR: server/app.py no encontrado en {SERVER_SCRIPT}")
        sys.exit(1)

    # Heredar todas las variables de entorno (PORT, DATA_DIR, etc. inyectadas por Pinokio)
    env = os.environ.copy()

    # Garantizar que DATA_DIR y PLUGIN_DIR usen rutas absolutas
    if "DATA_DIR" not in env or not env["DATA_DIR"]:
        env["DATA_DIR"] = str(BASE_DIR / "data")
    if "PLUGIN_DIR" not in env or not env["PLUGIN_DIR"]:
        env["PLUGIN_DIR"] = str(BASE_DIR)
    if "OLLAMA_URL" not in env:
        env["OLLAMA_URL"] = "http://127.0.0.1:11434"

    # Forzar encoding UTF-8
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # En Windows, agregar herramientas portables al PATH
    if IS_WINDOWS:
        env = setup_windows_path(env)

    log(f"Plataforma: {sys.platform}")
    log(f"Python venv: {VENV_PYTHON}")
    log(f"Servidor: {SERVER_SCRIPT}")
    log(f"PORT: {env.get('PORT', '8000')}")
    log(f"DATA_DIR: {env['DATA_DIR']}")

    # Ejecutar server/app.py con el Python del venv
    result = subprocess.run(
        [str(VENV_PYTHON), str(SERVER_SCRIPT)],
        env=env,
        cwd=str(BASE_DIR)
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
