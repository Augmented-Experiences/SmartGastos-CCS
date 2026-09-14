// App de escritorio nativa SmartSuite (Tauri v2)
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::io::{BufRead, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use tauri::{Emitter, Manager, RunEvent, Url, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

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
struct Navigated(Mutex<bool>);
struct LastProbe(Mutex<String>);

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

fn ollama_command() -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new("ollama");
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
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
    if let Some(child) = state.0.lock().unwrap().take() {
        ollama_log("kill_backend_child: terminando sidecar backend");
        match child.kill() {
            Ok(()) => ollama_log("kill_backend_child: OK"),
            Err(e) => ollama_log(&format!("kill_backend_child: {}", e)),
        }
    }
}

fn shutdown_app(app: &tauri::AppHandle, reason: &str) {
    ollama_log(&format!("shutdown_app: {}", reason));
    kill_backend_child(&*app.state::<BackendState>());
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

fn wait_for_port(port: u16, attempts: u32) -> bool {
    for _ in 0..attempts {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
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
                if body.contains("\"indexExists\":false") || body.contains("\"indexExists\": false") {
                    return ProbeResult {
                        ok: false,
                        detail: "sidecar sin app/index.html empaquetado (rebuild PyInstaller)".into(),
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
                    detail: format!(
                        "GET / status {} (no HTML). head={:?}",
                        status,
                        snippet
                    ),
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
        s.phase = "ready".into();
        if s.message.starts_with("Descargando") || s.message.starts_with("Componente") {
            s.message = "Servicios listos.".into();
        }
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
        probe.ok,
        probe.detail
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

fn pull_model_with_progress(app: &tauri::AppHandle, model: &str) -> bool {
    let body = format!("{{\"name\":\"{}\"}}", model);
    let resp = ureq::post("http://127.0.0.1:11434/api/pull")
        .set("Content-Type", "application/json")
        .send_string(&body);
    let resp = match resp {
        Ok(r) => r,
        Err(_) => return false,
    };
    let reader = std::io::BufReader::new(resp.into_reader());
    let mut ok = false;
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let v: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if v.get("error").is_some() {
            ok = false;
            break;
        }
        let status = v.get("status").and_then(|s| s.as_str()).unwrap_or("");
        let total = v.get("total").and_then(|t| t.as_u64());
        let completed = v.get("completed").and_then(|c| c.as_u64());
        let pct: i32 = match (total, completed) {
            (Some(t), Some(c)) if t > 0 => ((c.min(t) * 100) / t) as i32,
            _ => -1,
        };
        let msg = if pct >= 0 {
            format!("Descargando el modelo {} - {}%", model, pct)
        } else {
            format!("Preparando el modelo {} ({})...", model, status)
        };
        ollama_log(&msg);
        update_status(app, |s| {
            s.phase = "downloading".into();
            s.message = msg.clone();
            s.percent = pct;
        });
        if status == "success" {
            ok = true;
        }
    }
    ok
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

fn bootstrap_ollama(app: tauri::AppHandle, backend_port: u16) {
    std::thread::spawn(move || {
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
            return;
        }

        if TcpStream::connect(("127.0.0.1", 11434)).is_err() {
            let mut cmd = ollama_command();
            cmd.arg("serve").stdout(Stdio::null()).stderr(Stdio::null());
            let _ = cmd.spawn();
            wait_for_port(11434, 30);
        }

        let model = model_for_ram();
        pull_model_with_progress(&app, &model);

        for extra in &app_config().extra_models {
            pull_model_with_progress(&app, extra);
            ollama_log(&format!("Modelo adicional '{}' listo.", extra));
            update_status(&app, |s| {
                s.message = format!("Componente {} listo.", extra);
                s.percent = 100;
            });
        }

        ollama_log("extra_models terminado; entrando a finish_ollama_bootstrap");
        finish_ollama_bootstrap(&app, backend_port);
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
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

            if let Some(w) = handle.get_webview_window("main") {
                let app_for_window = handle.clone();
                w.on_window_event(move |event| {
                    match event {
                        WindowEvent::CloseRequested => {
                            shutdown_app(&app_for_window, "ventana main CloseRequested");
                        }
                        WindowEvent::Destroyed => {
                            shutdown_app(&app_for_window, "ventana main Destroyed");
                            app_for_window.exit(0);
                        }
                        _ => {}
                    }
                });
            }

            kill_backend_child(&*app.state::<BackendState>());
            let port = pick_port();
            app.manage(BackendPort(port));
            ollama_log(&format!("Puerto backend elegido: {}", port));

            let data_dir = user_data_dir().to_string_lossy().to_string();
            if app.state::<BackendState>().0.lock().unwrap().is_some() {
                ollama_log("WARN: sidecar ya registrado; omitiendo segundo spawn");
            }
            let sidecar = app.shell().sidecar("backend");
            match sidecar {
                Ok(cmd) => match cmd
                    .env("PORT", port.to_string())
                    .env("DATA_DIR", data_dir)
                    .env("RUN_BY_TAURI", "1")
                    .env("PYTHONIOENCODING", "utf-8")
                    .spawn()
                {
                    Ok((mut rx, child)) => {
                        let mut slot = app.state::<BackendState>().0.lock().unwrap();
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
                            shutdown_app(&sidecar_handle, "sidecar stdout/stderr EOF");
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

            bootstrap_ollama(handle.clone(), port);

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
                    if matches!(event, WindowEvent::CloseRequested) {
                        shutdown_app(app_handle, "RunEvent::WindowEvent CloseRequested");
                    }
                    if matches!(event, WindowEvent::Destroyed) {
                        shutdown_app(app_handle, "RunEvent::WindowEvent Destroyed");
                        app_handle.exit(0);
                    }
                }
                _ => {}
            }
        });
}
