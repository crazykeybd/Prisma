#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::Manager;

struct Backend(Mutex<Option<Child>>);
struct Port(Mutex<u16>);

fn debug_log(root: &std::path::Path, msg: &str) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join("gui_debug.log"))
    {
        let _ = writeln!(f, "{}", msg);
    }
}

fn find_prisma_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?.to_path_buf();
    for _ in 0..8 {
        if dir.join("config").join("config.yaml").exists() {
            return Some(dir);
        }
        if dir.join("prisma").join("config").join("config.yaml").exists() {
            return Some(dir.join("prisma"));
        }
        dir = dir.parent()?.to_path_buf();
    }
    None
}

fn find_python(root: &std::path::Path) -> PathBuf {
    let bundled = root.join("python-runtime").join("python.exe");
    if bundled.exists() {
        return bundled;
    }
    let venv = root.join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        return venv;
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let base = PathBuf::from(local).join("Programs").join("Python");
        if let Ok(entries) = std::fs::read_dir(&base) {
            let mut found: Vec<PathBuf> = entries
                .flatten()
                .map(|e| e.path().join("python.exe"))
                .filter(|p| p.exists())
                .collect();
            found.sort();
            if let Some(p) = found.pop() {
                return p;
            }
        }
    }
    PathBuf::from("python")
}

fn spawn_backend(root: &std::path::Path, port: u16) -> Result<Child, String> {
    let server_args = |py: &PathBuf| -> Vec<String> {
        if py.file_name().map(|n| n == "py").unwrap_or(false) {
            vec!["-3".into(), "-m".into(), "api.server".into(), "--port".into(), port.to_string()]
        } else {
            vec!["-m".into(), "api.server".into(), "--port".into(), port.to_string()]
        }
    };
    let candidates = [find_python(root), PathBuf::from("python"), PathBuf::from("py")];
    let mut last_err = String::new();
    for py in candidates.iter() {
        let mut command = Command::new(py);
        command
            .args(server_args(py))
            .current_dir(root)
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        // Keep backend failures visible after the desktop window has closed.
        let log_dir = root.join("log");
        let _ = std::fs::create_dir_all(&log_dir);
        if let Ok(file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_dir.join("backend.log"))
        {
            if let Ok(error_file) = file.try_clone() {
                command.stdout(Stdio::from(file)).stderr(Stdio::from(error_file));
            }
        }

        // python.exe is a console-subsystem program. Without this flag Windows
        // creates a console window even when the Tauri parent has none.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        match command.spawn() {
            Ok(c) => return Ok(c),
            Err(e) => last_err = format!("{py:?}: {e}"),
        }
    }
    Err(format!("failed to start python backend ({last_err})"))
}

#[tauri::command]
fn pick_file(kind: String) -> Option<String> {
    let mut dlg = rfd::FileDialog::new();
    dlg = match kind.as_str() {
        "exec" => dlg.add_filter("Executable", &["exe"]),
        "input" => dlg
            .add_filter("RELAP5 输入卡", &["i", "inp", "txt", "dat", "relap", "deck"])
            .add_filter("All Files", &["*"]),
        "output" => dlg
            .add_filter("RELAP5 输出文件", &["out", "outdta", "txt", "log", "dat"])
            .add_filter("All Files", &["*"]),
        _ => dlg.add_filter("Excel", &["xlsx"]),
    };
    dlg.pick_file().map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
fn pick_project_manifest() -> Result<Option<String>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("打开项目 - 选择项目根目录中的 project.json")
        .add_filter("Prisma 项目", &["json"])
        .pick_file()
    else {
        return Ok(None);
    };

    let is_manifest = path
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| name.eq_ignore_ascii_case("project.json"))
        .unwrap_or(false);
    if !is_manifest {
        return Err("请选择项目根目录中的 project.json".to_string());
    }

    let root = path
        .parent()
        .filter(|parent| parent.is_dir())
        .ok_or_else(|| "project.json 所在的项目文件夹不存在".to_string())?;
    Ok(Some(root.to_string_lossy().to_string()))
}

#[tauri::command]
fn open_software_library(path: String) -> Result<(), String> {
    let root = PathBuf::from(path);
    if !root.is_dir() {
        return Err("软件库文件夹不存在，请刷新软件状态后重试".to_string());
    }
    Command::new("explorer.exe")
        .arg(&root)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开软件库文件夹: {error}"))
}

#[tauri::command]
fn open_report(path: String) -> Result<(), String> {
    let report = PathBuf::from(path);
    if !report.is_absolute() {
        return Err("测试报告路径必须是绝对路径".to_string());
    }
    if !report.is_file() {
        return Err("测试报告文件不存在".to_string());
    }
    let is_html = report
        .extension()
        .and_then(|extension| extension.to_str())
        .map(|extension| extension.eq_ignore_ascii_case("html"))
        .unwrap_or(false);
    if !is_html {
        return Err("只能打开 HTML 测试报告".to_string());
    }
    Command::new("explorer.exe")
        .arg(&report)
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开测试报告: {error}"))
}

#[tauri::command]
fn get_port(state: tauri::State<Port>) -> u16 {
    *state.0.lock().unwrap()
}

/// Minimal HTTP/1.1 proxy so the webview never talks to the backend directly.
/// Some environments fail `fetch("http://127.0.0.1:...")` inside WebView2 even
/// when the backend is reachable from everywhere else.
#[tauri::command]
fn api_proxy(
    state: tauri::State<Port>,
    path: String,
    method: Option<String>,
    body: Option<String>,
) -> Result<String, String> {
    let port = *state.0.lock().unwrap();
    let method = method.unwrap_or_else(|| "GET".to_string());
    let body = body.unwrap_or_default();
    let mut stream = TcpStream::connect(format!("127.0.0.1:{port}")).map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(300)))
        .map_err(|e| e.to_string())?;
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        body.as_bytes().len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw);
    let sep = text.find("\r\n\r\n").ok_or("invalid http response")?;
    Ok(text[sep + 4..].to_string())
}

fn main() {
    let root_opt = find_prisma_root();
    if let Some(root) = &root_opt {
        debug_log(root, "main: entered");
    }

    // Dedicated WebView2 user-data folder: avoids "resource in use" conflicts
    // between app instances and with other WebView2-based applications.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let udf = dir.join("webview2-data");
            if std::fs::create_dir_all(&udf).is_ok() {
                std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", &udf);
            }
        }
    }

    let app = tauri::Builder::default()
        .setup(move |app| {
            let root = root_opt.clone().ok_or_else(|| "prisma root not found".to_string())?;
            debug_log(&root, "setup: begin");
            let port = TcpListener::bind("127.0.0.1:0")
                .and_then(|l| l.local_addr())
                .map_err(|e| e.to_string())?
                .port();
            debug_log(&root, &format!("setup: spawning backend port={port}"));
            let mut child = spawn_backend(&root, port)?;
            debug_log(&root, &format!("setup: backend pid={}", child.id()));
            // Wait until the backend actually accepts connections (Python cold
            // start can take seconds). Otherwise the webview fires its first
            // fetch before the server is listening -> "Failed to fetch".
            let addr = format!("127.0.0.1:{port}");
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
            loop {
                if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                    return Err(format!("backend exited early: {status}").into());
                }
                if std::net::TcpStream::connect(&addr).is_ok() {
                    debug_log(&root, "setup: backend accepting connections");
                    break;
                }
                if std::time::Instant::now() > deadline {
                    return Err("backend did not start listening within 20s".into());
                }
                std::thread::sleep(std::time::Duration::from_millis(150));
            }
            app.manage(Backend(Mutex::new(Some(child))));
            app.manage(Port(Mutex::new(port)));
            debug_log(&root, "setup: done");
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            pick_file,
            pick_project_manifest,
            open_software_library,
            open_report,
            get_port,
            api_proxy
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    if let Some(root) = find_prisma_root() {
        debug_log(&root, "build: done");
    }

    app.run(|_app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = _app_handle.try_state::<Backend>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(child) = guard.as_mut() {
                        let _ = child.kill();
                    }
                }
            }
        }
    });
}
