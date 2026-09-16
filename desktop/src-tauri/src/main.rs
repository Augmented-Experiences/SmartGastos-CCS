// App de escritorio nativa SmartSuite (Tauri v2)
#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::io::Write;
use std::net::TcpListener;
use std::path::PathBuf;
<<<<<<< ours
<<<<<<< ours
use std::process::Child;
=======
use std::process::{Child, Command, Stdio};
>>>>>>> theirs
=======
use std::process::{Child, Command, Stdio};
>>>>>>> theirs
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use tauri::{Emitter, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

mod ollama;

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct Tier {
    max_ram_gb: f64,
    model: String,
}

#[derive(serde::Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppConfig {
    product_name: String,
    data_dir_name: String,
    ollama_tiers: Vec<Tier>,
    #[serde(default)]
    extra_models: Vec<String>,
}

static APP_CONFIG_JSON: &str = include_str!("../appconfig.json");

fn app_config() -> &'static AppConfig {
    static CFG: OnceLock<AppConfig> = OnceLock::new();
    CFG.get_or_init(|| serde_json::from_str(APP_CONFIG_JSON).expect("appconfig.json invalido"))
}

struct BackendState(Mutex<Option<CommandChild>>);
/// Solo contiene el proceso `ollama serve` lanzado por esta instancia.
struct OllamaState(Mutex<Option<Child>>);
struct ShutdownState(AtomicBool);
struct Navigated(Mutex<bool>);
struct LastProbe(Mutex<String>);
/// Puerto HTTP de Ollama para esta sesión (11434 u otro loopback).
#[allow(dead_code)]
struct OllamaListenPort(u16);

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct Status {
    phase: String,
    message: String,
    percent: i32,
    backend_url: Option<String>,
    can_continue: bool,
    ollama_done: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    backend_error: Option<String>,
}

impl Status {
    fn initial() -> Self {
        Status {
            phase: "starting".into(),
            message: "Iniciando servicios...".into(),
            percent: -1,
            backend_url: None,
            can_continue: false,
            ollama_done: false,
            backend_error: None,
        }
    }
}

struct AppStatus(Mutex<Status>);
struct BackendPort(u16);

fn persist_status_snapshot(snapshot: &Status) {
    let path = user_data_dir().join("desktop_status.json");
    if let Ok(json) = serde_json::to_string(snapshot) {
        let _ = std::fs::write(path, json);
    }
}

fn update_status(app: &tauri::AppHandle, f: impl FnOnce(&mut Status)) {
    let snapshot = {
        let state = app.state::<AppStatus>();
        let mut s = state.0.lock().unwrap();
        f(&mut s);
        s.clone()
    };
    persist_status_snapshot(&snapshot);
    let _ = app.emit("status", snapshot);
}

#[tauri::command]
fn current_status(state: tauri::State<AppStatus>) -> Status {
    state.0.lock().unwrap().clone()
}

#[tauri::command]
fn navigate_to_backend(
    app: tauri::AppHandle,
    port: tauri::State<BackendPort>,
) -> Result<(), String> {
    navigate_main_to_backend(&app, port.0)
}

#[tauri::command]
fn retry_backend(app: tauri::AppHandle, port: tauri::State<BackendPort>) -> Result<String, String> {
    let p = port.0;
    if try_mark_backend_ready(&app, p) {
        navigate_main_to_backend(&app, p)?;
        Ok(backend_app_url(p))
    } else {
        let detail = app.state::<LastProbe>().0.lock().unwrap().clone();
        Err(format!(
            "El servidor aun no responde con la UI. Diagnostico: {}",
            detail
        ))
    }
}

fn home_dir() -> PathBuf {
    #[cfg(windows)]
    {
        std::env::var("USERPROFILE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
    }
    #[cfg(not(windows))]
    {
        std::env::var("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
    }
}

fn user_data_dir() -> PathBuf {
    let app = app_config().data_dir_name.as_str();
    #[cfg(target_os = "windows")]
    {
        let base = std::env::var("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home_dir().join("AppData").join("Roaming"));
        base.join(app)
    }
    #[cfg(target_os = "macos")]
    {
        home_dir()
            .join("Library")
            .join("Application Support")
            .join(app)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let base = std::env::var("XDG_DATA_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| home_dir().join(".local").join("share"));
        base.join(app)
    }
}

fn log_dir() -> PathBuf {
    let dir = user_data_dir().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn ollama_log(line: &str) {
    let path = log_dir().join("ollama.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(f, "{}", line);
    }
}

/// Mismo archivo que los mensajes de Ollama (`logs/ollama.log` bajo APPDATA/SmartGastos).
fn backend_log(line: &str) {
    ollama_log(line);
}

fn kill_backend_child(state: &BackendState) {
    let child = {
        let mut slot = state.0.lock().unwrap();
        slot.take()
    };
    if let Some(child) = child {
        ollama_log("kill_backend_child: terminando sidecar backend");
        match child.kill() {
            Ok(()) => ollama_log("kill_backend_child: OK"),
            Err(e) => ollama_log(&format!("kill_backend_child: {}", e)),
        }
    }
}

fn kill_ollama_child(state: &OllamaState) {
    let child = {
        let mut slot = state.0.lock().unwrap();
        slot.take()
    };
    if let Some(mut child) = child {
<<<<<<< ours
<<<<<<< ours
        let pid = child.id();
        ollama_log(&format!(
            "kill_ollama_child: terminando Ollama iniciado por esta app (pid={})",
            pid
        ));
        ollama::kill_owned_child(&mut child);
        ollama::clear_owned_pid(&user_data_dir());
        ollama_log("kill_ollama_child: OK");
=======
=======
>>>>>>> theirs
        ollama_log("kill_ollama_child: terminando Ollama iniciado por esta app");
        match child.kill() {
            Ok(()) => {
                let _ = child.wait();
                ollama_log("kill_ollama_child: OK");
            }
            Err(e) => ollama_log(&format!("kill_ollama_child: {}", e)),
        }
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
    }
}

fn shutdown_requested(app: &tauri::AppHandle) -> bool {
    app.state::<ShutdownState>().0.load(Ordering::Acquire)
}

fn shutdown_app(app: &tauri::AppHandle, reason: &str) {
    if app.state::<ShutdownState>().0.swap(true, Ordering::AcqRel) {
        return;
    }
    ollama_log(&format!("shutdown_app: {}", reason));
    // Bind the state before acquiring its Mutex to avoid a temporary State borrow.
    let backend_state = app.state::<BackendState>();
    kill_backend_child(&backend_state);
    let ollama_state = app.state::<OllamaState>();
    kill_ollama_child(&ollama_state);
}

fn pick_port() -> u16 {
    for p in [7860u16, 7861, 7862, 7863] {
        if TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return p;
        }
    }
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(7860)
}

fn backend_app_url(port: u16) -> String {
    format!("http://127.0.0.1:{}/", port)
}

fn splash_url(port: u16) -> String {
    format!("http://127.0.0.1:{}/__splash/", port)
}

fn wait_for_health(port: u16, attempts: u32) -> bool {
    let health_url = format!("http://127.0.0.1:{}/api/health", port);
    for _ in 0..attempts {
        match ureq::get(&health_url).call() {
            Ok(r) if r.status() == 200 => {
                ollama_log("GET /api/health 200 — sidecar arriba");
                return true;
            }
            Ok(r) => ollama_log(&format!("GET /api/health status {}", r.status())),
            Err(e) => ollama_log(&format!("GET /api/health error: {}", e)),
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn navigate_webview_external(app: &tauri::AppHandle, url_str: &str) -> Result<(), String> {
    let parsed = Url::parse(url_str).map_err(|e| format!("URL invalida: {}", e))?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "ventana main no encontrada".to_string())?;
    window
        .navigate(parsed)
        .map_err(|e| format!("navigate fallo: {}", e))?;
    ollama_log(&format!("WebView navigate OK -> {}", url_str));
    Ok(())
}

fn open_splash_on_backend(app: &tauri::AppHandle, port: u16) {
    let url = splash_url(port);
    let handle = app.clone();
    ollama_log(&format!("abriendo splash same-origin: {}", url));
    match app.run_on_main_thread(move || {
        if let Some(w) = handle.get_webview_window("main") {
            let _ = w.show();
            let _ = w.set_focus();
        } else {
            ollama_log("open_splash_on_backend: ventana main no encontrada antes de navigate");
        }
        if let Err(e) = navigate_webview_external(&handle, &url) {
            ollama_log(&format!("open_splash_on_backend navigate: {}", e));
        }
    }) {
        Ok(()) => ollama_log("open_splash_on_backend: run_on_main_thread OK"),
        Err(e) => ollama_log(&format!("open_splash_on_backend: run_on_main_thread {}", e)),
    }
}

struct ProbeResult {
    ok: bool,
    detail: String,
}

fn probe_backend(port: u16) -> ProbeResult {
    let health_url = format!("http://127.0.0.1:{}/api/health", port);
    let root_url = backend_app_url(port);
    let health_status = match ureq::get(&health_url).call() {
        Ok(r) => r.status(),
        Err(e) => {
            return ProbeResult {
                ok: false,
                detail: format!("GET /api/health error: {}", e),
            };
        }
    };
    if health_status != 200 {
        return ProbeResult {
            ok: false,
            detail: format!("GET /api/health status {}", health_status),
        };
    }

    let ui_url = format!("http://127.0.0.1:{}/api/desktop-ui", port);
    if let Ok(r) = ureq::get(&ui_url).call() {
        if r.status() == 200 {
            if let Ok(body) = r.into_string() {
                ollama_log(&format!("/api/desktop-ui: {}", body));
                if body.contains("\"indexExists\":false") || body.contains("\"indexExists\": false")
                {
                    return ProbeResult {
                        ok: false,
                        detail: "sidecar sin app/index.html empaquetado (rebuild PyInstaller)"
                            .into(),
                    };
                }
            }
        }
    }

    match ureq::get(&root_url).call() {
        Ok(r) => {
            let status = r.status();
            let body = r.into_string().unwrap_or_default();
            let snippet: String = body.chars().take(120).collect();
            if status == 200 && (body.contains("<!DOCTYPE") || body.contains("<html")) {
                ProbeResult {
                    ok: true,
                    detail: "health 200 + HTML en /".into(),
                }
            } else {
                ProbeResult {
                    ok: false,
                    detail: format!("GET / status {} (no HTML). head={:?}", status, snippet),
                }
            }
        }
        Err(e) => ProbeResult {
            ok: false,
            detail: format!("GET / error: {}", e),
        },
    }
}

fn wait_for_backend_ready(app: &tauri::AppHandle, port: u16, attempts: u32) -> bool {
    for _ in 0..attempts {
        let probe = probe_backend(port);
        *app.state::<LastProbe>().0.lock().unwrap() = probe.detail.clone();
        if probe.ok {
            ollama_log(&format!("sidecar listo: {}", probe.detail));
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn apply_backend_ready(app: &tauri::AppHandle, port: u16) {
    let url = backend_app_url(port);
    update_status(app, |s| {
        s.backend_url = Some(url);
        s.can_continue = true;
        s.backend_error = None;
        if s.ollama_done {
            s.phase = "ready".into();
        }
        // No pisar el progreso de descarga/extracción/arranque/pull de Ollama.
    });
}

fn try_mark_backend_ready(app: &tauri::AppHandle, port: u16) -> bool {
    let probe = probe_backend(port);
    *app.state::<LastProbe>().0.lock().unwrap() = probe.detail.clone();
    ollama_log(&format!("probe try_mark_backend_ready: {}", probe.detail));
    if probe.ok {
        apply_backend_ready(app, port);
        true
    } else {
        false
    }
}

fn navigate_main_to_backend(app: &tauri::AppHandle, port: u16) -> Result<(), String> {
    if *app.state::<Navigated>().0.lock().unwrap() {
        return Ok(());
    }
    let probe = probe_backend(port);
    *app.state::<LastProbe>().0.lock().unwrap() = probe.detail.clone();
    ollama_log(&format!(
        "navigate_main_to_backend probe: ok={} {}",
        probe.ok, probe.detail
    ));
    if !probe.ok {
        update_status(app, |s| {
            s.phase = "warning".into();
            s.backend_error = Some(format!(
                "El servidor local no respondio: {}. Revise logs/ollama.log y reconstruya backend.exe.",
                probe.detail
            ));
        });
        ollama_log("navigate_main_to_backend: omitido (health/UI probe fallo)");
        return Err(probe.detail);
    }
    let _ = try_mark_backend_ready(app, port);
    let url_str = backend_app_url(port);
    navigate_webview_external(app, &url_str)?;
    *app.state::<Navigated>().0.lock().unwrap() = true;
    Ok(())
}

fn model_for_ram() -> String {
    let mut sys = sysinfo::System::new();
    sys.refresh_memory();
    let gb = sys.total_memory() as f64 / 1024.0 / 1024.0 / 1024.0;
    let cfg = app_config();
    for t in &cfg.ollama_tiers {
        if t.max_ram_gb > 0.0 && gb < t.max_ram_gb {
            return t.model.clone();
        }
    }
    cfg.ollama_tiers
        .last()
        .map(|t| t.model.clone())
        .unwrap_or_else(|| "llama3.2:3b".to_string())
}

fn status_progress(app: &tauri::AppHandle, phase: &str, percent: i32, message: &str) {
    ollama_log(message);
    update_status(app, |s| {
        s.phase = phase.into();
        s.message = message.into();
        s.percent = percent;
    });
}

fn take_owned_slot(app: &tauri::AppHandle, mut child: Child) -> bool {
    let pid = child.id();
    let ollama_state = app.state::<OllamaState>();
    let mut slot = ollama_state.0.lock().unwrap();
    if shutdown_requested(app) {
        drop(slot);
        ollama::kill_owned_child(&mut child);
        ollama::clear_owned_pid(&user_data_dir());
        return false;
    }
    if let Some(mut previous) = slot.replace(child) {
        ollama::kill_owned_child(&mut previous);
    }
    ollama::record_owned_pid(&user_data_dir(), pid);
    ollama_log(&format!(
        "Ollama portable iniciado por esta instancia (pid={})",
        pid
    ));
    true
}

fn pull_model_with_progress(app: &tauri::AppHandle, port: u16, model: &str) -> bool {
    ollama::pull_model(port, model, |pct, msg| {
        status_progress(app, "downloading", pct, msg);
    })
}

fn finish_ollama_bootstrap(app: &tauri::AppHandle, backend_port: u16) {
    ollama_log("== finish_ollama_bootstrap (post extra_models)");
    let _ = try_mark_backend_ready(app, backend_port);

    update_status(app, |s| {
        s.ollama_done = true;
        s.percent = 100;
        s.message = "Modelos listos. Abriendo la aplicacion...".into();
        if s.can_continue {
            s.phase = "ready".into();
            if s.backend_url.is_none() {
                s.backend_url = Some(backend_app_url(backend_port));
            }
        } else {
            let detail = app.state::<LastProbe>().0.lock().unwrap().clone();
            s.backend_error = Some(format!(
                "UI aun no lista: {}. Use Entrar en el splash.",
                detail
            ));
        }
    });
    ollama_log("ollama_done=true; splash debe hacer location.replace('/') via /api/desktop-status");
}

fn bootstrap_ollama(
    app: tauri::AppHandle,
    backend_port: u16,
    ollama_port: u16,
    already_healthy: bool,
) {
    std::thread::spawn(move || {
        if shutdown_requested(&app) {
<<<<<<< ours
<<<<<<< ours
=======
=======
>>>>>>> theirs
            return;
        }
        update_status(&app, |s| {
            s.phase = "ollama".into();
            s.message = "Verificando el motor de IA (Ollama)...".into();
            s.percent = -1;
        });

        let installed = ollama_command()
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !installed {
            finish_ollama_bootstrap(&app, backend_port);
>>>>>>> theirs
            return;
        }
        status_progress(&app, "ollama", -1, "Verificando el motor de IA (Ollama)...");

<<<<<<< ours
        let data_dir = user_data_dir();
        if already_healthy {
            ollama_log(&format!(
                "Usando Ollama ya en marcha en {} (no se registra PID propio)",
                ollama::api_base(ollama_port)
            ));
        } else {
            match ollama::ensure_portable_binary(&data_dir, |pct, msg| {
                let phase = if msg.starts_with("Extrayendo") {
                    "extract"
                } else {
                    "download"
                };
                status_progress(&app, phase, pct, msg);
            }) {
                Ok(bin) => {
                    if shutdown_requested(&app) {
                        return;
                    }
                    if ollama::api_healthy(ollama_port) {
                        ollama_log(
                            "Ollama respondio en el puerto elegido antes de spawn; no se toma PID",
                        );
                    } else {
                        status_progress(&app, "start", -1, "Iniciando el servicio de IA...");
                        match ollama::spawn_serve(&bin, ollama_port, &ollama::models_dir(&data_dir))
                        {
                            Ok(child) => {
                                if !take_owned_slot(&app, child) {
                                    return;
                                }
                            }
                            Err(e) => {
                                ollama_log(&format!("No se pudo iniciar Ollama portable: {}", e));
                                status_progress(
                                    &app,
                                    "warning",
                                    -1,
                                    "No se pudo iniciar Ollama. La app continuara sin IA.",
                                );
                                finish_ollama_bootstrap(&app, backend_port);
                                return;
                            }
                        }
                        if !ollama::wait_until_healthy(ollama_port, 60) {
                            ollama_log("Ollama portable no respondio a /api/tags");
                            status_progress(
                                &app,
                                "warning",
                                -1,
                                "Ollama no respondio a tiempo. La app continuara sin IA.",
                            );
                            finish_ollama_bootstrap(&app, backend_port);
                            return;
                        }
                        ollama_log(&format!(
                            "Ollama portable listo en {}",
                            ollama::api_base(ollama_port)
                        ));
                    }
                }
                Err(e) => {
                    ollama_log(&format!("Ollama portable no disponible: {}", e));
                    status_progress(
                        &app,
                        "warning",
                        -1,
                        "No se pudo descargar Ollama. La app continuara sin IA.",
                    );
=======
        if TcpStream::connect(("127.0.0.1", 11434)).is_err() {
            let mut cmd = ollama_command();
            cmd.arg("serve").stdout(Stdio::null()).stderr(Stdio::null());
            match cmd.spawn() {
                Ok(mut child) => {
                    let ollama_state = app.state::<OllamaState>();
                    let mut slot = ollama_state.0.lock().unwrap();
                    if shutdown_requested(&app) {
                        drop(slot);
                        let _ = child.kill();
                        let _ = child.wait();
                        return;
                    }
                    if let Some(mut previous) = slot.replace(child) {
                        let _ = previous.kill();
                        let _ = previous.wait();
                    }
                    ollama_log("Ollama iniciado por esta instancia");
                }
                Err(e) => {
                    ollama_log(&format!("No se pudo iniciar Ollama: {}", e));
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
                    finish_ollama_bootstrap(&app, backend_port);
                    return;
                }
            }
<<<<<<< ours
<<<<<<< ours
        }

        if shutdown_requested(&app) {
            return;
        }
        if !ollama::api_healthy(ollama_port) {
            ollama_log("API de Ollama no saludable; se omite pull");
            finish_ollama_bootstrap(&app, backend_port);
            return;
=======
=======
>>>>>>> theirs
            wait_for_port(11434, 30);
>>>>>>> theirs
        }

        if shutdown_requested(&app) {
            return;
        }
        let model = model_for_ram();
        let _ = pull_model_with_progress(&app, ollama_port, &model);

        for extra in &app_config().extra_models {
            if shutdown_requested(&app) {
                return;
            }
            let _ = pull_model_with_progress(&app, ollama_port, extra);
            ollama_log(&format!("Modelo adicional '{}' listo.", extra));
            status_progress(
                &app,
                "downloading",
                100,
                &format!("Componente {} listo.", extra),
            );
        }

        ollama_log("extra_models terminado; entrando a finish_ollama_bootstrap");
        finish_ollama_bootstrap(&app, backend_port);
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
        .manage(OllamaState(Mutex::new(None)))
        .manage(ShutdownState(AtomicBool::new(false)))
        .manage(AppStatus(Mutex::new(Status::initial())))
        .manage(Navigated(Mutex::new(false)))
        .manage(LastProbe(Mutex::new(String::new())))
        .invoke_handler(tauri::generate_handler![
            current_status,
            retry_backend,
            navigate_to_backend
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            *handle.state::<Navigated>().0.lock().unwrap() = false;
            persist_status_snapshot(&Status::initial());

            let backend_state = app.state::<BackendState>();
            kill_backend_child(&backend_state);
            let port = pick_port();
            app.manage(BackendPort(port));
            ollama_log(&format!("Puerto backend elegido: {}", port));

            let (ollama_port, ollama_already_healthy) = ollama::decide_listen_port();
            app.manage(OllamaListenPort(ollama_port));
            ollama_log(&format!(
                "Ollama HTTP {} (ya saludable={})",
                ollama::api_base(ollama_port),
                ollama_already_healthy
            ));

            let data_dir = user_data_dir().to_string_lossy().to_string();
            let backend_state = app.state::<BackendState>();
            if backend_state.0.lock().unwrap().is_some() {
                ollama_log("WARN: sidecar ya registrado; omitiendo segundo spawn");
            }
            let ollama_url = ollama::api_base(ollama_port);
            let ollama_host = format!("127.0.0.1:{}", ollama_port);
            let sidecar = app.shell().sidecar("backend");
            match sidecar {
                Ok(cmd) => match cmd
                    .env("PORT", port.to_string())
                    .env("DATA_DIR", data_dir)
                    .env("RUN_BY_TAURI", "1")
                    .env("PYTHONIOENCODING", "utf-8")
                    .env("OLLAMA_URL", ollama_url)
                    .env("OLLAMA_HOST", ollama_host)
                    .spawn()
                {
                    Ok((mut rx, child)) => {
                        let backend_state = app.state::<BackendState>();
                        let mut slot = backend_state.0.lock().unwrap();
                        if shutdown_requested(&handle) {
                            drop(slot);
                            let _ = child.kill();
                            return Ok(());
                        }
                        if slot.is_some() {
                            ollama_log("WARN: sidecar slot ocupado; matando instancia duplicada");
                            let _ = slot.take().map(|c| c.kill());
                        }
                        slot.replace(child);
                        let sidecar_handle = handle.clone();
                        tauri::async_runtime::spawn(async move {
                            while let Some(event) = rx.recv().await {
                                if let CommandEvent::Stderr(bytes) | CommandEvent::Stdout(bytes) =
                                    event
                                {
                                    backend_log(&String::from_utf8_lossy(&bytes));
                                }
                            }
                            ollama_log("sidecar backend: canal de eventos cerrado (proceso terminó?)");
                            let backend_state = sidecar_handle.state::<BackendState>();
                            kill_backend_child(&backend_state);
                        });
                    }
                    Err(e) => {
                        backend_log(&format!("spawn error: {}", e));
                        update_status(&handle, |s| {
                            s.phase = "warning".into();
                            s.message = format!("No se pudo iniciar el servidor: {}", e);
                            s.backend_error = Some(s.message.clone());
                        });
                    }
                },
                Err(e) => {
                    backend_log(&format!("sidecar missing: {}", e));
                    update_status(&handle, |s| {
                        s.phase = "warning".into();
                        s.message = format!("Backend no encontrado en el instalador: {}", e);
                        s.backend_error = Some(s.message.clone());
                    });
                }
            }

            let splash_handle = handle.clone();
            std::thread::spawn(move || {
                if wait_for_health(port, 120) {
                    open_splash_on_backend(&splash_handle, port);
                } else {
                    ollama_log(
                        "ERROR: /api/health no respondio; sidecar caido (revise traceback en este log)",
                    );
                    update_status(&splash_handle, |s| {
                        s.phase = "warning".into();
                        s.message = "El servidor local no inicio.".into();
                        s.backend_error = Some(
                            "El sidecar (backend.exe) termino antes de escuchar. \
                             Reconstruya con build-backend.ps1 y revise logs/ollama.log."
                                .into(),
                        );
                    });
                }
            });

            bootstrap_ollama(handle.clone(), port, ollama_port, ollama_already_healthy);

            let ready_handle = handle.clone();
            std::thread::spawn(move || {
                if wait_for_backend_ready(&ready_handle, port, 3600) {
                    apply_backend_ready(&ready_handle, port);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error al construir la app de escritorio")
        .run(|app_handle, event| {
            match event {
                RunEvent::ExitRequested { .. } => {
                    shutdown_app(app_handle, "RunEvent::ExitRequested");
                }
                RunEvent::Exit => {
                    shutdown_app(app_handle, "RunEvent::Exit");
                }
                RunEvent::WindowEvent { label, event, .. } if label == "main" => {
                    match event {
                        WindowEvent::CloseRequested { .. } => {
                            shutdown_app(app_handle, "RunEvent::WindowEvent CloseRequested");
                            app_handle.exit(0);
                        }
                        WindowEvent::Destroyed => {
                            shutdown_app(app_handle, "RunEvent::WindowEvent Destroyed");
                            app_handle.exit(0);
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        });
}
