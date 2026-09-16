"""
setup.py — Script de instalación cross-platform para pyme-ledger-ai.
Ejecutado por install.json. Funciona en macOS, Windows y Linux.

Responsabilidades:
  1. Crear el venv si no existe
  2. Instalar dependencias Python con `python -m pip` (NO pip.exe directo)
  3. Instalar Tesseract OCR automáticamente según plataforma
  4. Instalar Poppler (para PDF→imagen) automáticamente según plataforma
  5. Crear estructura de directorios de datos
  6. Copiar defaults si no existen

NOTAS DE COMPATIBILIDAD WINDOWS:
  - NUNCA usar pip.exe directamente → usar `python -m pip` (pip no puede
    actualizarse a sí mismo en Windows porque el .exe está bloqueado)
  - NUNCA usar rutas con / en subprocess → usar str(Path(...)) que genera \\
  - NUNCA asumir que herramientas están en PATH → buscar en rutas comunes
  - Usar encoding="utf-8" en todos los open()
  - Usar ensure_ascii=False en json.dumps para preservar ñ/tildes
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ── Rutas absolutas ────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
VENV_DIR     = BASE_DIR / "venv"
DEFAULTS_DIR = BASE_DIR / "defaults"
DATA_DIR     = BASE_DIR / "data"

IS_WINDOWS = sys.platform == "win32"
IS_MACOS   = sys.platform == "darwin"
IS_LINUX   = sys.platform.startswith("linux")

# Rutas del venv según plataforma
if IS_WINDOWS:
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"

# Modelo IA único del plugin
AI_MODEL = "qwen3:0.6b"


def log(msg: str):
    print(f"[setup] {msg}", flush=True)


def run(cmd: list, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Ejecuta un comando. Si check=True lanza excepción si falla."""
    cmd_str = [str(c) for c in cmd]
    log(f"$ {' '.join(cmd_str)}")
    result = subprocess.run(cmd_str, **kwargs)
    if check and result.returncode != 0:
        raise RuntimeError(f"Comando falló (código {result.returncode}): {cmd_str}")
    return result


def run_silent(cmd: list, timeout: int = 300) -> bool:
    """Ejecuta un comando silenciosamente. Retorna True si tuvo éxito."""
    try:
        cmd_str = [str(c) for c in cmd]
        r = subprocess.run(cmd_str, capture_output=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


# ── 1. Crear venv ──────────────────────────────────────────────────────────────
def create_venv():
    if VENV_PYTHON.exists():
        log(f"Venv ya existe en {VENV_DIR}")
        return
    log(f"Creando venv en {VENV_DIR} ...")
    run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not VENV_PYTHON.exists():
        raise RuntimeError(f"Venv creado pero python no encontrado en {VENV_PYTHON}")
    log("Venv creado.")


# ── 2. Instalar dependencias Python ───────────────────────────────────────────
def install_python_deps():
    """
    Instala dependencias usando `python -m pip` (NO pip.exe directo).
    
    En Windows, pip.exe no puede actualizarse a sí mismo porque el ejecutable
    está bloqueado por el sistema operativo. Usar `python -m pip` evita este
    problema completamente.
    """
    # El launcher instala el mismo subconjunto liviano que el sidecar Tauri:
    # no incluye EasyOCR ni Torch; el OCR visual usa Ollama/moondream.
    req_file = BASE_DIR / "requirements-desktop.txt"
    if not req_file.exists():
        req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        raise RuntimeError(f"Archivo de dependencias no encontrado en {req_file}")

    # Paso 1: Actualizar pip usando python -m pip (NUNCA pip.exe directo)
    log("Actualizando pip ...")
    run([
        str(VENV_PYTHON), "-m", "pip", "install",
        "--upgrade", "pip",
        "--no-warn-script-location",
        "--quiet"
    ], check=False)  # No fallar si pip ya está actualizado

    # Paso 2: Instalar el subconjunto de dependencias del instalador.
    log(f"Instalando {req_file.name} ...")
    run([
        str(VENV_PYTHON), "-m", "pip", "install",
        "-r", str(req_file),
        "--no-warn-script-location"
    ])
    log("Dependencias Python instaladas.")


# ── 3. Instalar Tesseract OCR ─────────────────────────────────────────────────
def install_tesseract():
    """Instala Tesseract OCR automáticamente según la plataforma."""
    # Verificar si ya está disponible
    if shutil.which("tesseract"):
        log("Tesseract ya está instalado.")
        return True

    # En Windows, buscar en rutas comunes (puede estar instalado pero no en PATH)
    if IS_WINDOWS:
        common_tess_paths = [
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tesseract-OCR",
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tesseract-OCR",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR",
        ]
        for tess_dir in common_tess_paths:
            tess_exe = tess_dir / "tesseract.exe"
            if tess_exe.exists():
                os.environ["PATH"] = str(tess_dir) + os.pathsep + os.environ.get("PATH", "")
                log(f"Tesseract encontrado en {tess_dir}")
                return True

    log("Instalando Tesseract OCR...")

    if IS_MACOS:
        if shutil.which("brew"):
            ok = run_silent(["brew", "install", "tesseract"])
            if ok:
                log("Tesseract instalado via Homebrew.")
                return True
        log("AVISO: Instala manualmente: brew install tesseract")
        return False

    elif IS_LINUX:
        if shutil.which("apt-get"):
            ok = run_silent(["sudo", "apt-get", "install", "-y",
                             "tesseract-ocr", "tesseract-ocr-spa", "tesseract-ocr-eng"])
            if ok:
                log("Tesseract instalado via apt-get.")
                return True
        elif shutil.which("dnf"):
            ok = run_silent(["sudo", "dnf", "install", "-y",
                             "tesseract", "tesseract-langpack-spa"])
            if ok:
                log("Tesseract instalado via dnf.")
                return True
        log("AVISO: sudo apt-get install tesseract-ocr tesseract-ocr-spa")
        return False

    elif IS_WINDOWS:
        # Intentar winget
        if shutil.which("winget"):
            ok = run_silent(["winget", "install", "-e", "--id",
                             "UB-Mannheim.TesseractOCR",
                             "--accept-source-agreements",
                             "--accept-package-agreements"])
            if ok:
                # Buscar en rutas comunes después de instalar
                for tess_dir in common_tess_paths:
                    if (tess_dir / "tesseract.exe").exists():
                        os.environ["PATH"] = str(tess_dir) + os.pathsep + os.environ.get("PATH", "")
                        log("Tesseract instalado via winget.")
                        return True

        # Intentar choco
        if shutil.which("choco"):
            ok = run_silent(["choco", "install", "tesseract", "-y"])
            if ok:
                log("Tesseract instalado via Chocolatey.")
                return True

        log("AVISO: Tesseract no disponible. OCR usará EasyOCR como alternativa.")
        log("Para mejor precisión: https://github.com/UB-Mannheim/tesseract/wiki")
        return False

    return False


# ── 4. Instalar Poppler ───────────────────────────────────────────────────────
def install_poppler():
    """Instala Poppler (necesario para convertir PDFs a imágenes)."""
    if shutil.which("pdftoppm") or shutil.which("pdfinfo"):
        log("Poppler ya está instalado.")
        return True

    # En Windows, verificar tools/poppler portable primero
    if IS_WINDOWS:
        poppler_dir = BASE_DIR / "tools" / "poppler"
        # Buscar pdftoppm.exe en subdirectorios
        for candidate in poppler_dir.rglob("pdftoppm.exe") if poppler_dir.exists() else []:
            bin_dir = str(candidate.parent)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            log(f"Poppler portable encontrado en {bin_dir}")
            return True

    log("Instalando Poppler (para procesamiento de PDFs)...")

    if IS_MACOS:
        if shutil.which("brew"):
            ok = run_silent(["brew", "install", "poppler"])
            if ok:
                log("Poppler instalado via Homebrew.")
                return True
        log("AVISO: brew install poppler")
        return False

    elif IS_LINUX:
        if shutil.which("apt-get"):
            ok = run_silent(["sudo", "apt-get", "install", "-y", "poppler-utils"])
            if ok:
                log("Poppler instalado via apt-get.")
                return True
        elif shutil.which("dnf"):
            ok = run_silent(["sudo", "dnf", "install", "-y", "poppler-utils"])
            if ok:
                log("Poppler instalado via dnf.")
                return True
        log("AVISO: sudo apt-get install poppler-utils")
        return False

    elif IS_WINDOWS:
        # Intentar winget/choco
        if shutil.which("winget"):
            ok = run_silent(["winget", "install", "-e", "--id",
                             "freedesktop.Poppler",
                             "--accept-source-agreements",
                             "--accept-package-agreements"])
            if ok and shutil.which("pdftoppm"):
                log("Poppler instalado via winget.")
                return True

        if shutil.which("choco"):
            ok = run_silent(["choco", "install", "poppler", "-y"])
            if ok and shutil.which("pdftoppm"):
                log("Poppler instalado via Chocolatey.")
                return True

        # Fallback: descargar binarios portables
        try:
            import zipfile
            import urllib.request

            poppler_dir = BASE_DIR / "tools" / "poppler"
            zip_path = BASE_DIR / "tools" / "poppler.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)

            poppler_url = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
            log("Descargando Poppler portable para Windows...")
            urllib.request.urlretrieve(poppler_url, str(zip_path))

            log("Extrayendo Poppler...")
            with zipfile.ZipFile(str(zip_path), 'r') as z:
                z.extractall(str(poppler_dir))
            zip_path.unlink(missing_ok=True)

            # Buscar pdftoppm.exe en la extracción
            for bin_candidate in poppler_dir.rglob("pdftoppm.exe"):
                real_bin = str(bin_candidate.parent)
                os.environ["PATH"] = real_bin + os.pathsep + os.environ.get("PATH", "")
                log(f"Poppler portable instalado en {real_bin}")
                return True
        except Exception as e:
            log(f"Error descargando Poppler: {e}")

        log("AVISO: Poppler no disponible. Preview de PDFs no funcionará.")
        log("Descarga desde https://github.com/oschwartz10612/poppler-windows/releases")
        return False

    return False


# ── 5. Crear estructura de datos ──────────────────────────────────────────────
def create_data_dirs():
    dirs = [
        DATA_DIR / "uploads",
        DATA_DIR / "uploads" / "thumbnails",
        DATA_DIR / "exports",
        DATA_DIR / "sessions",
        DATA_DIR / "agents",
        DATA_DIR / "learning",
        DATA_DIR / "prompts" / "system",
        DATA_DIR / "prompts" / "templates",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    log("Directorios de datos creados.")


# ── 6. Copiar defaults ────────────────────────────────────────────────────────
def copy_defaults():
    if not DEFAULTS_DIR.exists():
        log("No hay directorio defaults/ — saltando.")
        return

    agents_src = DEFAULTS_DIR / "agents.json"
    agents_dst = DATA_DIR / "agents" / "agents.json"
    if agents_src.exists() and not agents_dst.exists():
        agents_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(agents_src), str(agents_dst))
        log("agents.json copiado.")

    prompts_src = DEFAULTS_DIR / "prompts"
    prompts_dst = DATA_DIR / "prompts"
    if prompts_src.exists():
        for src_file in prompts_src.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(prompts_src)
                dst_file = prompts_dst / rel
                if not dst_file.exists():
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src_file), str(dst_file))
        log("Prompts por defecto copiados.")


# ── Resumen de instalación ────────────────────────────────────────────────────
def write_install_summary(results: dict):
    """Guarda un resumen de la instalación para diagnóstico."""
    import datetime
    summary = {
        "platform": sys.platform,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "arch": platform.machine(),
        "installed_at": datetime.datetime.now().isoformat(),
        "ai_model": AI_MODEL,
        "base_dir": str(BASE_DIR),
        "venv_python": str(VENV_PYTHON),
        "components": results
    }
    summary_path = DATA_DIR / "install_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(summary_path), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    log(f"Resumen guardado en {summary_path}")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log(f"=== Iniciando setup en {sys.platform} / {platform.machine()} ===")
    log(f"=== Directorio base: {BASE_DIR} ===")
    log(f"=== Python ejecutable: {sys.executable} ===")
    log(f"=== Modelo IA: {AI_MODEL} ===")

    results = {}
    errors = []

    # Paso 1: Crear venv
    try:
        log("\n--- Paso 1/6: Creando entorno virtual Python ---")
        create_venv()
        results['venv'] = 'ok'
    except Exception as e:
        log(f"ERROR en venv: {e}")
        errors.append(f"venv: {e}")
        # Sin venv no podemos continuar
        sys.exit(1)

    # Paso 2: Instalar dependencias (CRÍTICO: usa python -m pip, NO pip.exe)
    try:
        log("\n--- Paso 2/6: Instalando dependencias Python ---")
        install_python_deps()
        results['python_deps'] = 'ok'
    except Exception as e:
        log(f"ERROR en dependencias Python: {e}")
        errors.append(f"python_deps: {e}")
        # Sin deps no podemos continuar
        sys.exit(1)

    # Paso 3: Tesseract (opcional — no bloquea la instalación)
    try:
        log("\n--- Paso 3/6: Instalando Tesseract OCR ---")
        ok = install_tesseract()
        results['tesseract'] = 'ok' if ok else 'warning_not_installed'
    except Exception as e:
        log(f"AVISO en Tesseract: {e}")
        results['tesseract'] = f'error: {e}'

    # Paso 4: Poppler (opcional — no bloquea la instalación)
    try:
        log("\n--- Paso 4/6: Instalando Poppler (PDF→imagen) ---")
        ok = install_poppler()
        results['poppler'] = 'ok' if ok else 'warning_not_installed'
    except Exception as e:
        log(f"AVISO en Poppler: {e}")
        results['poppler'] = f'error: {e}'

    # Paso 5: Directorios de datos
    try:
        log("\n--- Paso 5/6: Creando directorios de datos ---")
        create_data_dirs()
        results['data_dirs'] = 'ok'
    except Exception as e:
        log(f"ERROR en directorios: {e}")
        errors.append(f"data_dirs: {e}")

    # Paso 6: Copiar defaults
    try:
        log("\n--- Paso 6/6: Copiando configuración por defecto ---")
        copy_defaults()
        results['defaults'] = 'ok'
    except Exception as e:
        log(f"AVISO en defaults: {e}")
        results['defaults'] = f'warning: {e}'

    # Resumen final
    write_install_summary(results)

    if errors:
        log(f"\n=== Setup completado con errores: {errors} ===")
        sys.exit(1)
    else:
        log("\n=== Setup completado exitosamente ===")
        log(f"Componentes: {results}")
        sys.exit(0)
