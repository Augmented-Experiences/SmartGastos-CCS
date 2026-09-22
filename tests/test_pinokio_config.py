"""
test_pinokio_config.py — Tests para archivos de configuración Pinokio
======================================================================
Verifica que todos los archivos de configuración cumplan con las
buenas prácticas del skill pinokio-plugin-dev:
  - pinokio.js: kernel.exists + kernel.script.running (no fs.existsSync)
  - start.json: local.set + browser.open, sin localhost
  - stop.json: shell.run cross-platform (no script.stop)
  - install.json: venv + pip + ollama pull
  - defaults/agents.json: alineado con pipeline real
"""

import unittest
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()


class TestPinokioJs(unittest.TestCase):
    """Tests para pinokio.js."""

    def setUp(self):
        self.path = REPO_ROOT / "pinokio.js"
        self.content = self.path.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_uses_kernel_exists(self):
        """Debe usar kernel.exists en vez de fs.existsSync."""
        self.assertIn("kernel.exists", self.content)
        self.assertNotIn("fs.existsSync", self.content)

    def test_uses_kernel_script_running(self):
        """Debe usar kernel.script.running en vez de info.running."""
        self.assertIn("kernel.script.running", self.content)

    def test_no_require_fs(self):
        """No debe importar fs (usa API de Pinokio)."""
        self.assertNotIn("require(\"fs\")", self.content)
        self.assertNotIn("require('fs')", self.content)

    def test_has_install_start_stop(self):
        """Debe referenciar install.json, start.json, stop.json."""
        self.assertIn("install.json", self.content)
        self.assertIn("start.json", self.content)
        self.assertIn("stop.json", self.content)

    def test_has_title_and_icon(self):
        """Debe tener title e icon."""
        self.assertIn("title:", self.content)
        self.assertIn("icon:", self.content)

    def test_version_format(self):
        """Versión debe ser semver."""
        import re
        match = re.search(r'version:\s*"(\d+\.\d+\.\d+)"', self.content)
        self.assertIsNotNone(match, "Versión debe ser semver x.y.z")


class TestStartJson(unittest.TestCase):
    """Tests para start.json."""

    def setUp(self):
        self.path = REPO_ROOT / "start.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_is_daemon(self):
        """Debe ser daemon: true."""
        self.assertTrue(self.data.get("daemon"))

    def test_has_local_set(self):
        """Debe tener local.set para capturar la URL."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertIn("local.set", methods)

    def test_has_browser_open(self):
        """Debe tener browser.open para abrir la UI."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertIn("browser.open", methods)

    def test_no_localhost_in_env(self):
        """No debe usar 'localhost' en env (usar 127.0.0.1)."""
        content = self.path.read_text(encoding="utf-8")
        # Buscar localhost en env pero no en comentarios
        self.assertNotIn('"localhost"', content)

    def test_has_cross_platform_ollama_start(self):
        """Debe tener inicio de Ollama cross-platform (when platform)."""
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("platform", content)

    def test_has_venv(self):
        """Debe usar venv para aislar dependencias."""
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("venv", content)


class TestStopJson(unittest.TestCase):
    """Tests para stop.json."""

    def setUp(self):
        self.path = REPO_ROOT / "stop.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_no_script_stop(self):
        """NO debe usar script.stop (prohibido por el skill)."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertNotIn("script.stop", methods)

    def test_uses_shell_run(self):
        """Debe usar shell.run para detener procesos."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertIn("shell.run", methods)

    def test_cross_platform(self):
        """Debe tener ramas para win32 y no-win32."""
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("win32", content)

    def test_has_feedback_log(self):
        """Debe tener log de confirmación."""
        methods = [step.get("method") for step in self.data.get("run", [])]
        self.assertIn("log", methods)


class TestInstallJson(unittest.TestCase):
    """Tests para install.json."""

    def setUp(self):
        self.path = REPO_ROOT / "install.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_has_run_steps(self):
        """Debe tener pasos de ejecución."""
        self.assertIn("run", self.data)
        self.assertGreater(len(self.data["run"]), 0)

    def test_uses_setup_py(self):
        """Debe usar setup.py para configurar entorno (venv + requirements)."""
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("setup.py", content)

    def test_uses_install_ollama(self):
        """Debe usar install_ollama.py para instalar Ollama."""
        content = self.path.read_text(encoding="utf-8")
        self.assertIn("install_ollama", content)


class TestDefaultsAgentsJson(unittest.TestCase):
    """Tests para defaults/agents.json."""

    def setUp(self):
        self.path = REPO_ROOT / "defaults" / "agents.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_has_agents_key(self):
        """Debe tener clave 'agents'."""
        self.assertIn("agents", self.data)

    def test_has_required_agents(self):
        """Debe tener los 6 agentes del pipeline."""
        agent_ids = [a["id"] for a in self.data["agents"]]
        required = ["ocr", "vision", "extractor", "clasificador", "auditor", "recomendador"]
        for req in required:
            self.assertIn(req, agent_ids, f"Agente faltante: {req}")

    def test_agents_have_required_fields(self):
        """Cada agente debe tener campos requeridos."""
        for agent in self.data["agents"]:
            self.assertIn("id", agent)
            self.assertIn("nombre", agent)
            self.assertIn("model", agent)
            self.assertIn("tipo", agent)


class TestDefaultPrompts(unittest.TestCase):
    """Tests para prompts por defecto."""

    def test_general_prompt_has_security(self):
        """Prompt general debe tener cláusula de seguridad."""
        path = REPO_ROOT / "defaults" / "prompts" / "general.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            self.assertIn("INQUEBRANTABLES", content)

    def test_analyst_prompt_has_security(self):
        """Prompt de analista debe tener cláusula de seguridad."""
        path = REPO_ROOT / "defaults" / "prompts" / "analyst.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            self.assertIn("INQUEBRANTABLES", content)


class TestNoOldJsFiles(unittest.TestCase):
    """Verifica que no existan archivos .js obsoletos."""

    def test_no_install_js(self):
        self.assertFalse((REPO_ROOT / "install.js").exists())

    def test_no_start_js(self):
        self.assertFalse((REPO_ROOT / "start.js").exists())

    def test_no_stop_js(self):
        self.assertFalse((REPO_ROOT / "stop.js").exists())


class TestRequirementsTxt(unittest.TestCase):
    """Tests para requirements.txt."""

    def setUp(self):
        self.path = REPO_ROOT / "requirements.txt"
        self.content = self.path.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(self.path.exists())

    def test_has_fastapi(self):
        self.assertIn("fastapi", self.content.lower())

    def test_has_uvicorn(self):
        self.assertIn("uvicorn", self.content.lower())

    def test_has_sqlalchemy(self):
        self.assertIn("sqlalchemy", self.content.lower())

    def test_has_requests(self):
        self.assertIn("requests", self.content.lower())

    def test_has_pillow(self):
        """Pillow es necesario para procesamiento de imágenes."""
        self.assertIn("pillow", self.content.lower())


class TestUIFiles(unittest.TestCase):
    """Tests para archivos de UI."""

    def test_index_html_exists(self):
        self.assertTrue((REPO_ROOT / "app" / "index.html").exists())

    def test_icon_exists(self):
        self.assertTrue((REPO_ROOT / "icon.png").exists())

    def test_favicon_exists(self):
        self.assertTrue((REPO_ROOT / "app" / "favicon.ico").exists())
        self.assertTrue((REPO_ROOT / "desktop" / "src-tauri" / "icons" / "icon.ico").exists())

    def test_logo_exists(self):
        """Debe existir el wordmark CCS en color y el mark para iconos."""
        app_dir = REPO_ROOT / "app"
        self.assertTrue((app_dir / "logo-ccs.png").exists(), "Falta app/logo-ccs.png (wordmark color)")
        self.assertTrue(
            (REPO_ROOT / "desktop" / "brand" / "ccs-mark.png").exists(),
            "Falta desktop/brand/ccs-mark.png (mark CCS para iconos)",
        )
        has_legacy = (
            (app_dir / "logo-ccs.svg").exists()
            or (app_dir / "logo.svg").exists()
            or (app_dir / "logo.png").exists()
        )
        self.assertTrue(has_legacy, "Debe existir logo-ccs.svg, logo.svg o logo.png en app/")

    def test_desktop_smartsuite_config(self):
        cfg_path = REPO_ROOT / "desktop" / "smartsuite.config.json"
        self.assertTrue(cfg_path.exists(), "Falta desktop/smartsuite.config.json")
        import json
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg.get("publisher"), "Cámara de Comercio de Santiago")
        self.assertEqual(cfg.get("productName"), "SmartGastos")
        self.assertEqual(cfg.get("identifier"), "cl.ccs.smartgastos")
        self.assertEqual(cfg.get("accent"), "#00D53A")
        self.assertEqual(cfg.get("splashLogo"), "../app/logo-ccs.png")
        extra = (cfg.get("ollama") or {}).get("extraModels") or []
        self.assertIn("moondream", extra)

    def test_ui_uses_ccs_png_wordmark_and_favicon(self):
        index = (REPO_ROOT / "app" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="favicon.ico"', index)
        self.assertIn("logo-ccs.png", index)
        self.assertIn("ccs-about", index)
        canonical = (REPO_ROOT / "desktop" / "brand" / "splash.template.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("splash-mark.png", canonical)
        self.assertNotIn(".splash-mark", canonical)
        self.assertNotIn('class="splash-mark"', canonical)
        self.assertIn("splash-logo", canonical)
        self.assertIn("{{LOGO_HTML}}", canonical)
        self.assertIn('href="favicon.ico"', canonical)

    def test_ccs_palette_is_applied_to_splash_and_installer_theme(self):
        expected_gradient = (
            "linear-gradient(126.54deg, rgb(0, 215, 0) -3.03%, "
            "rgb(0, 86, 202) 56.75%)"
        )
        for relative_path in (
            "desktop/ui/splash.template.html",
            "desktop/ui/index.html",
        ):
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn(expected_gradient, content)
        for relative_path in ("desktop/brand/ccs-theme.css", "desktop/ui/ccs-theme.css"):
            content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn(expected_gradient, content)
            self.assertIn("#00D53A", content)
            self.assertIn("#002558", content)
            self.assertIn("#3A89DA", content)
        installer = (REPO_ROOT / "install.json").read_text(encoding="utf-8")
        self.assertIn(expected_gradient, installer)
        self.assertIn("#00D53A", installer)
        self.assertIn("#002558", installer)
        self.assertIn("#3A89DA", installer)

    def test_requirements_desktop_no_easyocr(self):
        lines = [
            ln.strip().lower()
            for ln in (REPO_ROOT / "requirements-desktop.txt").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        joined = "\n".join(lines)
        self.assertNotIn("easyocr", joined)
        self.assertNotIn("torch", joined)
        self.assertIn("rapidocr", joined)
        self.assertIn("onnxruntime", joined)

    def test_launcher_uses_lightweight_desktop_requirements(self):
        setup = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn('BASE_DIR / "requirements-desktop.txt"', setup)


class TestPortableOllama(unittest.TestCase):
    """La app de escritorio descarga Ollama portable; no usa MSI ni torch/OCR extra."""

    def setUp(self):
        src = REPO_ROOT / "desktop" / "src-tauri" / "src"
        self.main = (src / "main.rs").read_text(encoding="utf-8")
        self.ollama = (src / "ollama.rs").read_text(encoding="utf-8")
        self.joined = self.main + "\n" + self.ollama
        self.splash = (REPO_ROOT / "desktop" / "ui" / "splash.template.html").read_text(
            encoding="utf-8"
        )

    def test_downloads_official_windows_zip_and_linux_tgz(self):
        self.assertIn("ollama-windows-amd64.zip", self.ollama)
        self.assertIn("ollama-linux-amd64.tgz", self.ollama)
        self.assertIn("releases/latest/download", self.ollama)

    def test_never_runs_windows_system_installer(self):
        lowered = self.joined.lower()
        for banned in ("ollamasetup.exe", "winget", ".msi", "verysilent", "install.sh"):
            self.assertNotIn(banned, lowered, f"no debe invocar instalador de sistema ({banned})")

    def test_sidecar_kills_backend_process_tree(self):
        self.assertIn("kill_pid_tree", self.joined)
        self.assertIn("taskkill", self.joined.lower())
        self.assertIn("kill_pid_tree(pid)", self.main)
        self.assertIn("kill_listeners_on_port", self.joined)
        self.assertIn("decide_listen_port", self.joined)
        self.assertIn("api/tags", self.ollama)
        self.assertIn("owned.pid", self.ollama)
        self.assertIn("record_owned_pid", self.joined)
        self.assertIn("kill_owned_child", self.joined)
        self.assertNotIn("Command::new(\"pkill\")", self.joined)
        self.assertNotIn("pkill -f", self.joined)
        self.assertNotIn("cierre retenido", self.main)
        self.assertNotIn("api.prevent_exit()", self.main)
        self.assertIn("se cierra la ventana de todos modos", self.main)

    def test_sidecar_gets_ollama_url_and_continues_without_restart(self):
        self.assertIn('env("OLLAMA_URL"', self.main)
        self.assertIn("ensure_portable_binary", self.main)
        self.assertIn("spawn_serve", self.main)
        self.assertIn("finish_ollama_bootstrap", self.main)

    def test_sidecar_passes_ollama_model_env(self):
        self.assertIn('.env("OLLAMA_MODEL"', self.main)
        self.assertIn("active_model.txt", self.main)
        self.assertIn("let ram_model = model_for_ram()", self.main)

    def test_product_models_are_llama31_and_moondream(self):
        import json
        cfg = json.loads(
            (REPO_ROOT / "desktop" / "smartsuite.config.json").read_text(encoding="utf-8")
        )
        extra = (cfg.get("ollama") or {}).get("extraModels") or []
        tiers = (cfg.get("ollama") or {}).get("tiers") or []
        self.assertIn("moondream", extra)
        self.assertTrue(any(t.get("model") == "llama3.1:8b" for t in tiers))
        self.assertIn("extra_models", self.main)
        self.assertIn("model_for_ram", self.main)

    def test_linux_docker_build_script_exists(self):
        script = (REPO_ROOT / "desktop" / "scripts" / "linux-docker-build.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AppImage", script)
        self.assertIn(".deb", script)
        self.assertIn(".rpm", script)
        self.assertIn("build-backend.sh", script)
        self.assertIn("ubuntu", script.lower())

    def test_splash_shows_ollama_progress(self):
        self.assertIn("/api/desktop-status", self.splash)
        self.assertIn("s.message", self.splash)
        self.assertIn("s.percent", self.splash)
        self.assertIn("ollamaDone", self.splash)
        self.assertNotIn("Entrar ahora", self.splash)
        self.assertNotIn("id=\"enter\"", self.splash)
        self.assertIn("Cargando la aplicación...", self.splash)
        self.assertNotIn("Use Entrar en el splash", self.main)
        self.assertIn("Cargando la aplicación...", self.main)

    def test_desktop_requirements_still_exclude_easyocr_torch(self):
        lines = [
            ln.strip().lower()
            for ln in (REPO_ROOT / "requirements-desktop.txt").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        joined = "\n".join(lines)
        self.assertNotIn("easyocr", joined)
        self.assertNotIn("torch", joined)
        self.assertIn("rapidocr", joined)
        cfg = json.loads(
            (REPO_ROOT / "desktop" / "smartsuite.config.json").read_text(encoding="utf-8")
        )
        excludes = (cfg.get("pyinstaller") or {}).get("excludes") or []
        self.assertNotIn("cv2", excludes)
        self.assertIn("torch", excludes)


class TestSplashSingleWhiteLogo(unittest.TestCase):

    def test_splash_single_white_ccs_logo(self):
        """Un solo logo CCS blanco; configure nunca emite splash-mark.png."""
        self.assertTrue((REPO_ROOT / "desktop" / "brand" / "logo-ccs-white.png").exists())
        canonical = (REPO_ROOT / "desktop" / "brand" / "splash.template.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("splash-mark.png", canonical)
        self.assertNotIn(".splash-mark", canonical)
        self.assertNotIn('class="splash-mark"', canonical)
        self.assertIn("splash-logo", canonical)
        self.assertIn("{{LOGO_HTML}}", canonical)
        cfg_js = (REPO_ROOT / "desktop" / "scripts" / "configure.mjs").read_text(encoding="utf-8")
        self.assertIn("logo-ccs-white.png", cfg_js)
        self.assertIn("rmSync", cfg_js)
        self.assertIn("brand/splash.template.html", cfg_js)
        self.assertIn('features = ["tls"]', cfg_js)
        self.assertIn('zip = "0.6"', cfg_js)
        cargo = (REPO_ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('features = ["tls"]', cargo)
        self.assertIn('zip = "0.6"', cargo)
        rust = (REPO_ROOT / "desktop" / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn('.env("OLLAMA_MODEL"', rust)
        self.assertIn("active_model.txt", rust)
        extra = json.loads(
            (REPO_ROOT / "desktop" / "smartsuite.config.json").read_text(encoding="utf-8")
        )
        self.assertIn("moondream", (extra.get("ollama") or {}).get("extraModels") or [])


if __name__ == '__main__':
    unittest.main()
