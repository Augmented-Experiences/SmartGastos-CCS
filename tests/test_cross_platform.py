"""
test_cross_platform.py — Pruebas de compatibilidad Windows/macOS/Linux.
Verifica que todos los scripts y configuraciones funcionan correctamente
en las 3 plataformas simulando rutas y condiciones de cada una.

Ejecutar: python -m pytest tests/test_cross_platform.py -v
O simplemente: python tests/test_cross_platform.py
"""

import json
import os
import sys
import unittest
from pathlib import Path, PureWindowsPath, PurePosixPath
from unittest.mock import patch

# Agregar el directorio raíz al path
ROOT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(ROOT_DIR))


class TestInstallJson(unittest.TestCase):
    """Verifica que install.json tiene sintaxis correcta y no usa comandos Unix."""

    def setUp(self):
        with open(ROOT_DIR / "install.json", "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def test_valid_json(self):
        """install.json es JSON válido."""
        self.assertIsInstance(self.data, dict)
        self.assertIn("run", self.data)

    def test_no_unix_commands(self):
        """install.json no contiene comandos Unix directos."""
        unix_commands = ["mkdir -p", "cp ", "rm -", "sleep ", "curl ", "/dev/null",
                         "chmod ", "ln -s", "source ", "export "]
        content = json.dumps(self.data)
        for cmd in unix_commands:
            self.assertNotIn(cmd, content,
                             f"install.json contiene comando Unix: '{cmd}'")

    def test_uses_python_scripts(self):
        """install.json usa scripts Python (no shell directo)."""
        content = json.dumps(self.data)
        self.assertIn("python", content.lower())

    def test_no_background_true(self):
        """install.json no usa 'background: true' (no existe en Pinokio)."""
        content = json.dumps(self.data)
        self.assertNotIn('"background"', content)

    def test_no_pipe_operators(self):
        """install.json no usa pipes ni redirecciones shell en comandos."""
        # Solo verificar en los campos 'method: shell' o 'message'
        for step in self.data.get("run", []):
            if isinstance(step, dict) and step.get("method") == "shell.run":
                params = step.get("params", {})
                cmd = params.get("message", "")
                if isinstance(cmd, str):
                    self.assertNotIn(" | ", cmd,
                        f"Comando shell usa pipe: {cmd}")
                    self.assertNotIn(" 2>", cmd,
                        f"Comando shell usa redireccion: {cmd}")


class TestStartJson(unittest.TestCase):
    """Verifica que start.json tiene sintaxis correcta."""

    def setUp(self):
        with open(ROOT_DIR / "start.json", "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def test_valid_json(self):
        """start.json es JSON válido."""
        self.assertIsInstance(self.data, dict)
        self.assertIn("run", self.data)

    def test_has_daemon(self):
        """start.json tiene daemon: true."""
        self.assertTrue(self.data.get("daemon"))

    def test_platform_conditional_commands(self):
        """start.json usa bloques 'when' para comandos específicos de plataforma."""
        # Verificar que los comandos con /dev/null están dentro de when != win32
        for step in self.data.get('run', []):
            msg = step.get('params', {}).get('message', '')
            if '/dev/null' in msg:
                when = step.get('when', '')
                self.assertIn('win32', when,
                    "/dev/null debe estar dentro de un bloque when con condición de plataforma")

    def test_uses_127_0_0_1(self):
        """start.json usa 127.0.0.1 no localhost."""
        content = json.dumps(self.data)
        if "localhost" in content:
            self.fail("start.json usa 'localhost' en lugar de '127.0.0.1'")


class TestStopJson(unittest.TestCase):
    """Verifica que stop.json tiene sintaxis correcta."""

    def setUp(self):
        with open(ROOT_DIR / "stop.json", "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def test_valid_json(self):
        """stop.json es JSON válido."""
        self.assertIsInstance(self.data, dict)
        self.assertIn("run", self.data)

    def test_uses_shell_run(self):
        """stop.json usa shell.run (no script.stop, que está prohibido)."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertIn("shell.run", methods)
        self.assertNotIn("script.stop", methods)


class TestPinokioJs(unittest.TestCase):
    """Verifica que pinokio.js tiene la estructura correcta."""

    def setUp(self):
        with open(ROOT_DIR / "pinokio.js", "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_has_title(self):
        """pinokio.js tiene título."""
        self.assertIn("title:", self.content)

    def test_has_description(self):
        """pinokio.js tiene descripción."""
        self.assertIn("description:", self.content)

    def test_has_icon(self):
        """pinokio.js tiene icono."""
        self.assertIn("icon:", self.content)

    def test_has_menu(self):
        """pinokio.js tiene función menu."""
        self.assertIn("menu:", self.content)

    def test_cross_platform_venv_check(self):
        """pinokio.js usa kernel.exists para verificar venv (API Pinokio)."""
        self.assertIn("kernel.exists", self.content)

    def test_no_install_js_reference(self):
        """pinokio.js no referencia archivos .js para install/start."""
        # Solo debe referenciar .json
        self.assertNotIn('"install.js"', self.content)
        self.assertNotIn('"start.js"', self.content)
        self.assertNotIn('"stop.js"', self.content)


class TestSetupPy(unittest.TestCase):
    """Verifica que setup.py usa patrones cross-platform."""

    def setUp(self):
        with open(ROOT_DIR / "setup.py", "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_uses_python_m_pip(self):
        """setup.py usa 'python -m pip' no 'pip.exe' directo."""
        self.assertIn('"-m", "pip"', self.content)
        # No debe usar VENV_PIP directamente para instalar
        self.assertNotIn("VENV_PIP", self.content)

    def test_uses_pathlib(self):
        """setup.py usa pathlib para rutas (cross-platform)."""
        self.assertIn("from pathlib import Path", self.content)

    def test_no_hardcoded_unix_paths(self):
        """setup.py no tiene rutas Unix hardcodeadas."""
        # No debe tener /usr/bin, /tmp, etc.
        self.assertNotIn("/usr/bin", self.content)
        self.assertNotIn("/tmp/", self.content)

    def test_platform_detection(self):
        """setup.py detecta la plataforma."""
        self.assertIn("sys.platform", self.content)

    def test_encoding_utf8(self):
        """setup.py usa encoding utf-8 en archivos."""
        self.assertIn('encoding="utf-8"', self.content) or \
            self.assertIn("encoding='utf-8'", self.content)


class TestLauncherPy(unittest.TestCase):
    """Verifica que launcher.py es cross-platform."""

    def setUp(self):
        with open(ROOT_DIR / "launcher.py", "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_cross_platform_venv(self):
        """launcher.py detecta venv cross-platform."""
        self.assertIn("Scripts", self.content)
        self.assertIn("bin", self.content)

    def test_uses_127_0_0_1(self):
        """launcher.py usa 127.0.0.1 no localhost en codigo ejecutable."""
        self.assertIn("127.0.0.1", self.content)
        # Verificar que localhost no aparece en lineas de codigo (solo en comentarios)
        for i, line in enumerate(self.content.split("\n"), 1):
            stripped = line.strip()
            if "localhost" in stripped and not stripped.startswith("#") and not stripped.startswith('"""') and "(no localhost)" not in stripped:
                self.fail(f"launcher.py linea {i} usa localhost: {stripped}")

    def test_no_exec_or_fork(self):
        """launcher.py no usa os.exec* ni os.fork."""
        self.assertNotIn("os.exec", self.content)
        self.assertNotIn("os.fork", self.content)


class TestScriptsOllama(unittest.TestCase):
    """Verifica que los scripts de Ollama son cross-platform."""

    def test_install_ollama_cross_platform(self):
        """install_ollama.py tiene soporte para Windows y macOS."""
        with open(ROOT_DIR / "scripts" / "install_ollama.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("win32", content)
        self.assertIn("darwin", content)
        self.assertIn("127.0.0.1", content)
        self.assertNotIn("localhost", content)
        # Debe buscar en rutas comunes de Windows
        self.assertIn("LOCALAPPDATA", content)
        self.assertIn("Programs", content)

    def test_start_ollama_cross_platform(self):
        """start_ollama.py tiene soporte para Windows y macOS."""
        with open(ROOT_DIR / "scripts" / "start_ollama.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("win32", content)
        self.assertIn("CREATE_NO_WINDOW", content)
        self.assertIn("127.0.0.1", content)
        # Verificar que no usa curl como comando (solo en comentarios es OK)
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if "curl" in stripped and not stripped.startswith("#") and "no curl" not in stripped:
                self.fail(f"start_ollama.py linea {i} usa curl: {stripped}")
        # Verificar que no usa localhost en codigo
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if "localhost" in stripped and not stripped.startswith("#") and not stripped.startswith('"""') and "no curl" not in stripped:
                self.fail(f"start_ollama.py linea {i} usa localhost: {stripped}")

    def test_pull_models_uses_api(self):
        """pull_models.py usa API HTTP no CLI."""
        with open(ROOT_DIR / "scripts" / "pull_models.py", "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("urllib.request", content)
        self.assertIn("/api/pull", content)
        self.assertIn("127.0.0.1", content)
        self.assertNotIn("localhost", content)
        # No debe depender del CLI de ollama
        self.assertNotIn("subprocess.run([\"ollama\"", content)
        self.assertNotIn("shutil.which(\"ollama\")", content)


class TestRequirements(unittest.TestCase):
    """Verifica que requirements.txt no tiene paquetes problemáticos."""

    def setUp(self):
        with open(ROOT_DIR / "requirements.txt", "r", encoding="utf-8") as f:
            self.packages = [l.strip().lower() for l in f.readlines()
                             if l.strip() and not l.startswith("#")]

    def test_no_linux_only_packages(self):
        """requirements.txt no tiene paquetes solo-Linux."""
        linux_only = ["python-magic-bin", "uvloop", "gunicorn"]
        for pkg in linux_only:
            for line in self.packages:
                self.assertNotIn(pkg, line,
                                 f"requirements.txt tiene paquete Linux-only: {pkg}")

    def test_has_essential_packages(self):
        """requirements.txt tiene los paquetes esenciales."""
        content = "\n".join(self.packages)
        essentials = ["fastapi", "uvicorn", "pillow", "pdf2image"]
        for pkg in essentials:
            self.assertIn(pkg, content,
                          f"Falta paquete esencial: {pkg}")


class TestServerApp(unittest.TestCase):
    """Verifica que server/app.py no tiene problemas de compatibilidad."""

    def setUp(self):
        with open(ROOT_DIR / "server" / "app.py", "r", encoding="utf-8") as f:
            self.content = f.read()

    def test_uses_127_0_0_1(self):
        """server/app.py usa 127.0.0.1 no localhost."""
        # Permitir localhost solo en comentarios
        lines = self.content.split("\n")
        for i, line in enumerate(lines, 1):
            if "localhost" in line and not line.strip().startswith("#"):
                self.fail(f"server/app.py línea {i} usa 'localhost': {line.strip()}")

    def test_no_unix_specific_imports(self):
        """server/app.py no importa módulos Unix-only."""
        unix_modules = ["import fcntl", "import grp", "import pwd", "import resource"]
        for mod in unix_modules:
            self.assertNotIn(mod, self.content,
                             f"server/app.py importa módulo Unix-only: {mod}")


class TestNoOldJsFiles(unittest.TestCase):
    """Verifica que no existen archivos .js de scripts (solo pinokio.js)."""

    def test_no_install_js(self):
        """No existe install.js."""
        self.assertFalse((ROOT_DIR / "install.js").exists(),
                         "install.js existe — debe eliminarse (conflicta con install.json)")

    def test_no_start_js(self):
        """No existe start.js."""
        self.assertFalse((ROOT_DIR / "start.js").exists(),
                         "start.js existe — debe eliminarse (conflicta con start.json)")

    def test_no_stop_js(self):
        """No existe stop.js."""
        self.assertFalse((ROOT_DIR / "stop.js").exists(),
                         "stop.js existe — debe eliminarse (conflicta con stop.json)")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  PRUEBAS DE COMPATIBILIDAD CROSS-PLATFORM")
    print(f"  Plugin: SmartGastos")
    print(f"  Plataforma actual: {sys.platform}")
    print(f"{'='*60}\n")

    unittest.main(verbosity=2)
