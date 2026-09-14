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

use tauri::{Emitter, Manager, RunEvent, WebviewUrl};
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

#[derive(Clone, serde::Serialize)]
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

fn update_status(app: &tauri::AppHandle, f: impl FnOnce(&mut Status)) {
    let snapshot = {
        let state = app.state::<AppStatus>();
        let mut s = state.0.lock().unwrap();
        f(&mut s);
        s.clone()
    };
    let _ = app.emit("status", snapshot);
}

#[tauri::command]
fn current_status(state: tauri::State<AppStatus>) -> Status {
    state.0.lock().unwrap().clone()
}

#[tauri::command]
fn retry_backend(app: tauri::AppHandle, port: tauri::State<BackendPort>) -> Result<String, String> {
    let p = port.0;
    if try_mark_backend_ready(&app, p) {
        let url = backend_app_url(p);
        Ok(url)
    } else {
        Err("El servidor aun no responde. Espere unos segundos o cierre la app por completo y vuelva a abrirla.".into())
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

fn backend_log(line: &str) {
    let path = log_dir().join("backend.log");
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    {
        let _ = writeln!(f, "{}", line);
    }
}

fn kill_backend_child(state: &BackendState) {
    if let Some(child) = state.0.lock().unwrap().take() {
        let _ = child.kill();
    }
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

/// URL de la app web (SmartGastos sirve index y assets en `/`, no en `/ui/`).
fn backend_app_url(port: u16) -> String {
    format!("http://127.0.0.1:{}/", port)
}

fn http_ok(url: &str) -> bool {
    match ureq::get(url).call() {
        Ok(resp) => {
            let s = resp.status();
            s == 200 || s == 304 || s == 307 || s == 308
        }
        Err(_) => false,
    }
}

fn probe_backend_ready(port: u16) -> bool {
    let health = format!("http://127.0.0.1:{}/api/health", port);
    let root = backend_app_url(port);
    http_ok(&health) || http_ok(&root)
}

fn wait_for_backend_ready(port: u16, attempts: u32) -> bool {
    for _ in 0..attempts {
        if probe_backend_ready(port) {
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
    if probe_backend_ready(port) {
        apply_backend_ready(app, port);
        true
    } else {
        false
    }
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
    if !app.state::<AppStatus>().0.lock().unwrap().can_continue {
        try_mark_backend_ready(app, backend_port);
        if !app.state::<AppStatus>().0.lock().unwrap().can_continue {
            wait_for_backend_ready(backend_port, 120);
            try_mark_backend_ready(app, backend_port);
        }
    }

    update_status(app, |s| {
        s.ollama_done = true;
        if s.can_continue {
            s.phase = "ready".into();
            if s.backend_url.is_none() {
                s.backend_url = Some(backend_app_url(backend_port));
            }
        } else {
            s.phase = "warning".into();
            s.backend_error = Some(
                "El servidor no respondio (revise SmartGastos/logs en AppData). \
Pulse Entrar para reintentar o cierre la app por completo y vuelva a abrirla."
                    .into(),
            );
        }
    });
}

fn bootstrap_ollama(app: tauri::AppHandle, backend_port: u16) {
    std::thread::spawn(move || {
        update_status(&app, |s| {
            s.phase = "ollama".into();
            s.message = "Verificando el motor de IA (Ollama)...".into();
            s.percent = -1;
        });
        ollama_log(&format!(
            "== {}: preparando Ollama ==",
            app_config().product_name
        ));

        let installed = ollama_command()
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !installed {
            ollama_log("Ollama no instalado.");
            update_status(&app, |s| {
                s.phase = "warning".into();
                s.message =
                    "Ollama no esta instalado. La app abrira sin IA hasta que lo instale.".into();
                s.percent = -1;
            });
            finish_ollama_bootstrap(&app, backend_port);
            return;
        }

        if TcpStream::connect(("127.0.0.1", 11434)).is_err() {
            update_status(&app, |s| {
                s.message = "Iniciando el servicio de IA...".into();
                s.percent = -1;
            });
            let mut cmd = ollama_command();
            cmd.arg("serve").stdout(Stdio::null()).stderr(Stdio::null());
            let _ = cmd.spawn();
            wait_for_port(11434, 30);
        }

        let model = model_for_ram();
        update_status(&app, |s| {
            s.phase = "downloading".into();
            s.message = format!("Descargando el modelo {} (solo la primera vez)...", model);
            s.percent = -1;
        });
        let ok = pull_model_with_progress(&app, &model);
        if ok {
            update_status(&app, |s| {
                s.message = format!("Modelo {} listo.", model);
                s.percent = 100;
            });
        }

        for extra in &app_config().extra_models {
            update_status(&app, |s| {
                s.phase = "downloading".into();
                s.message = format!("Descargando componente de IA {}...", extra);
                s.percent = -1;
            });
            if pull_model_with_progress(&app, extra) {
                update_status(&app, |s| {
                    s.message = format!("Componente {} listo.", extra);
                    s.percent = 100;
                });
            }
        }

        finish_ollama_bootstrap(&app, backend_port);
    });
}

fn reset_splash_webview(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.navigate(WebviewUrl::App("index.html".into()));
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
        .manage(AppStatus(Mutex::new(Status::initial())))
        .invoke_handler(tauri::generate_handler![current_status, retry_backend])
        .setup(|app| {
            let handle = app.handle().clone();
            reset_splash_webview(&handle);

            kill_backend_child(app.state::<BackendState>());
            let port = pick_port();
            app.manage(BackendPort(port));
            backend_log(&format!("Puerto backend elegido: {}", port));

            let data_dir = user_data_dir().to_string_lossy().to_string();
            let sidecar = app.shell().sidecar("backend");
            match sidecar {
                Ok(cmd) => match cmd
                    .env("PORT", port.to_string())
                    .env("DATA_DIR", data_dir)
                    .spawn()
                {
                    Ok((mut rx, child)) => {
                        app.state::<BackendState>()
                            .0
                            .lock()
                            .unwrap()
                            .replace(child);
                        tauri::async_runtime::spawn(async move {
                            while let Some(event) = rx.recv().await {
                                if let CommandEvent::Stderr(bytes) | CommandEvent::Stdout(bytes) =
                                    event
                                {
                                    backend_log(&String::from_utf8_lossy(&bytes));
                                }
                            }
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

            bootstrap_ollama(handle.clone(), port);

            let ready_handle = handle.clone();
            std::thread::spawn(move || {
                if wait_for_backend_ready(port, 3600) {
                    apply_backend_ready(&ready_handle, port);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error al construir la app de escritorio")
        .run(|app_handle, event| {
            if matches!(event, RunEvent::Exit) {
                kill_backend_child(app_handle.state::<BackendState>());
            }
        });
}
