// App de escritorio nativa SmartSuite (Tauri v2)
//
// Arranca el backend FastAPI empaquetado (sidecar `backend`), prepara
// Ollama (servicio + descarga del modelo según la RAM) y muestra el progreso en
// la pantalla de carga. Cuando todo está listo, la ventana carga la UI real.
// Al cerrar la app, detiene el backend.
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::io::{BufRead, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// En Windows evita que se abra una ventana de consola al lanzar procesos.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

// --- Config por-herramienta (generada por scripts/configure.mjs) ---
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
    /// Modelos adicionales a descargar con progreso (p. ej. modelo de visión
    /// para OCR neuronal). Se descargan igual que el modelo del LLM.
    #[serde(default)]
    extra_models: Vec<String>,
}

static APP_CONFIG_JSON: &str = include_str!("../appconfig.json");

fn app_config() -> &'static AppConfig {
    static CFG: OnceLock<AppConfig> = OnceLock::new();
    CFG.get_or_init(|| serde_json::from_str(APP_CONFIG_JSON).expect("appconfig.json inválido"))
}

/// Proceso hijo del backend (para terminarlo al salir).
struct BackendState(Mutex<Option<CommandChild>>);

/// Estado compartido que se muestra en la pantalla de carga.
#[derive(Clone, serde::Serialize)]
struct Status {
    /// "starting" | "ollama" | "downloading" | "ready" | "warning"
    phase: String,
    message: String,
    /// 0–100, o -1 si es indeterminado.
    percent: i32,
    /// URL de la UI real (cuando el backend responde).
    backend_url: Option<String>,
    /// El backend ya responde: se puede entrar.
    can_continue: bool,
    /// El paso de Ollama terminó (éxito, omitido o fallo no fatal).
    ollama_done: bool,
}

impl Status {
    fn initial() -> Self {
        Status {
            phase: "starting".into(),
            message: "Iniciando servicios…".into(),
            percent: -1,
            backend_url: None,
            can_continue: false,
            ollama_done: false,
        }
    }
}

struct AppStatus(Mutex<Status>);

/// Puerto HTTP del sidecar FastAPI (fijado en `setup`).
struct BackendPort(u16);

/// Ruta que devuelve 200 cuando FastAPI está sirviendo (SmartGastos/SmartCaja).
const BACKEND_HEALTH_PATH: &str = "/api/health";
/// Entrada de la UI web empaquetada (StaticFiles en `server/app.py`).
const BACKEND_UI_PATH: &str = "/ui/";

/// Actualiza el estado compartido y lo emite a la pantalla de carga.
fn update_status(app: &tauri::AppHandle, f: impl FnOnce(&mut Status)) {
    let snapshot = {
        let state = app.state::<AppStatus>();
        let mut s = state.0.lock().unwrap();
        f(&mut s);
        s.clone()
    };
    let _ = app.emit("status", snapshot);
}

/// Comando invocable desde la pantalla de carga para obtener el estado actual
/// (evita perder actualizaciones si la página carga tarde).
#[tauri::command]
fn current_status(state: tauri::State<AppStatus>) -> Status {
    state.0.lock().unwrap().clone()
}

/// Comando `ollama` sin ventana de consola en Windows.
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

/// Carpeta de datos por-usuario (coincide con la del backend).
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

/// Añade una línea de estado al log de Ollama.
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

/// Elige un puerto libre para el backend (prefiere 7860, luego efímero).
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

/// Espera hasta que el puerto acepte conexiones (Ollama u otros servicios TCP).
fn wait_for_port(port: u16, attempts: u32) -> bool {
    for _ in 0..attempts {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn backend_health_url(port: u16) -> String {
    format!("http://127.0.0.1:{}{}", port, BACKEND_HEALTH_PATH)
}

fn backend_ui_url(port: u16) -> String {
    format!("http://127.0.0.1:{}{}", port, BACKEND_UI_PATH)
}

/// Espera hasta que `GET /api/health` responda 200 (no basta con TCP al puerto).
fn wait_for_backend_ready(port: u16, attempts: u32) -> bool {
    let url = backend_health_url(port);
    for _ in 0..attempts {
        if ureq::get(&url)
            .call()
            .map(|r| r.status() == 200)
            .unwrap_or(false)
        {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

fn apply_backend_ready(app: &tauri::AppHandle, port: u16) {
    let url = backend_ui_url(port);
    update_status(app, |s| {
        s.backend_url = Some(url);
        s.can_continue = true;
        if s.phase == "starting" || s.phase == "downloading" || s.phase == "ollama" {
            s.phase = "ready".into();
        }
        if s.message.is_empty() || s.message.starts_with("Descargando") || s.message.starts_with("Componente") {
            s.message = "Servicios listos.".into();
        }
    });
}

/// Modelo de Ollama recomendado según la RAM total del equipo y la config
/// por-herramienta (tiers en appconfig.json).
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

/// Descarga el modelo vía la API de Ollama (`/api/pull`) mostrando el progreso.
/// Devuelve true si el modelo quedó disponible.
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
            format!("Descargando el modelo {} — {}%", model, pct)
        } else {
            format!("Preparando el modelo {} ({})…", model, status)
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

/// Deja Ollama listo (best-effort) mostrando el progreso en la pantalla de carga.
fn bootstrap_ollama(app: tauri::AppHandle, backend_port: u16) {
    std::thread::spawn(move || {
        update_status(&app, |s| {
            s.phase = "ollama".into();
            s.message = "Verificando el motor de IA (Ollama)…".into();
            s.percent = -1;
        });
        ollama_log(&format!(
            "== {}: preparando el motor de IA (Ollama) ==",
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
            ollama_log("Ollama no está instalado. La app abrirá y la UI mostrará 'desconectado'.");
            update_status(&app, |s| {
                s.phase = "warning".into();
                s.message =
                    "Ollama no está instalado. La app abrirá; instálalo desde ollama.com/download para usar la IA."
                        .into();
                s.percent = -1;
                s.ollama_done = true;
            });
            return;
        }

        if TcpStream::connect(("127.0.0.1", 11434)).is_err() {
            update_status(&app, |s| {
                s.message = "Iniciando el servicio de IA…".into();
                s.percent = -1;
            });
            ollama_log("Iniciando el servicio 'ollama serve'…");
            let mut cmd = ollama_command();
            cmd.arg("serve").stdout(Stdio::null()).stderr(Stdio::null());
            let _ = cmd.spawn();
            wait_for_port(11434, 30);
        }
        ollama_log("Servicio Ollama disponible.");

        let model = model_for_ram();
        update_status(&app, |s| {
            s.phase = "downloading".into();
            s.message = format!("Descargando el modelo {} (solo la primera vez)…", model);
            s.percent = -1;
        });
        let ok = pull_model_with_progress(&app, &model);

        if ok {
            ollama_log(&format!("Modelo '{}' listo.", model));
            update_status(&app, |s| {
                s.message = format!("Modelo {} listo.", model);
                s.percent = 100;
            });
        } else {
            ollama_log(&format!("No se pudo descargar '{}' automáticamente.", model));
            update_status(&app, |s| {
                s.phase = "warning".into();
                s.message = format!(
                    "No se pudo descargar {} automáticamente; podrás reintentar desde la app.",
                    model
                );
                s.percent = -1;
            });
        }

        // Modelos adicionales (p. ej. visión para OCR neuronal), con el mismo progreso.
        for extra in &app_config().extra_models {
            update_status(&app, |s| {
                s.phase = "downloading".into();
                s.message = format!("Descargando componente de IA {} (solo la primera vez)…", extra);
                s.percent = -1;
            });
            ollama_log(&format!("Descargando modelo adicional '{}'…", extra));
            if pull_model_with_progress(&app, extra) {
                ollama_log(&format!("Modelo adicional '{}' listo.", extra));
                update_status(&app, |s| {
                    s.message = format!("Componente {} listo.", extra);
                    s.percent = 100;
                });
            } else {
                ollama_log(&format!("No se pudo descargar el modelo adicional '{}'.", extra));
                update_status(&app, |s| {
                    s.phase = "warning".into();
                    s.message = format!("No se pudo descargar {}; podrás reintentarlo luego.", extra);
                    s.percent = -1;
                });
            }
        }

        // Tras descargas largas (p. ej. moondream), el hilo de readiness puede haber
        // expirado antes de que el sidecar respondiera; volver a comprobar HTTP.
        let ready = app.state::<AppStatus>().0.lock().unwrap().can_continue;
        if !ready {
            if wait_for_backend_ready(backend_port, 600) {
                apply_backend_ready(&app, backend_port);
            }
        }

        update_status(&app, |s| {
            s.ollama_done = true;
            if s.can_continue && s.backend_url.is_some() {
                s.phase = "ready".into();
            }
        });
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendState(Mutex::new(None)))
        .manage(AppStatus(Mutex::new(Status::initial())))
        .invoke_handler(tauri::generate_handler![current_status])
        .setup(|app| {
            let handle = app.handle().clone();
            let port = pick_port();
            app.manage(BackendPort(port));

            // 1) Lanzar el backend empaquetado (sidecar), pasándole el puerto y la
            //    carpeta de datos por entorno (compatible con las apps del SmartSuite).
            let data_dir = user_data_dir().to_string_lossy().to_string();
            let (mut rx, child) = app
                .shell()
                .sidecar("backend")
                .expect("no se encontró el sidecar 'backend'")
                .env("PORT", port.to_string())
                .env("DATA_DIR", data_dir)
                .spawn()
                .expect("no se pudo iniciar el backend");
            app.state::<BackendState>()
                .0
                .lock()
                .unwrap()
                .replace(child);

            // Drenar la salida del backend (evita bloqueos del buffer).
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stderr(bytes) | CommandEvent::Stdout(bytes) = event {
                        let _ = String::from_utf8_lossy(&bytes);
                    }
                }
            });

            // 2) Preparar Ollama con progreso en pantalla.
            bootstrap_ollama(handle.clone(), port);

            // 3) Cuando FastAPI responda en /api/health, habilitar el ingreso a la UI.
            let ready_handle = handle.clone();
            std::thread::spawn(move || {
                // Hasta ~30 min (arranque lento + descargas Ollama en paralelo).
                if wait_for_backend_ready(port, 3600) {
                    apply_backend_ready(&ready_handle, port);
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error al construir la app de escritorio")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(child) = app_handle.state::<BackendState>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
