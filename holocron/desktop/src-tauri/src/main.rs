//! Vỏ Tauri của Holocron (Windows): chạy backend Python đi kèm làm sidecar rồi mở giao diện trong WebView2.
//!
//! - Token truy cập (`HOLOCRON_REQUIRE_TOKEN`) sinh ngẫu nhiên mỗi lần mở, chỉ nằm trong bộ nhớ của tiến trình này
//!   và env của sidecar; KHÔNG ghi log hay file, KHÔNG đưa vào JS. Tầng native WebView2 (`WebResourceRequested`) gắn
//!   `X-Holocron-Token` vào mọi request tới đúng `http://127.0.0.1:<cổng sidecar>/*`; trang ở nơi khác không nhận.
//! - Điều hướng chỉ cho trang chờ trong app và đúng `127.0.0.1:<cổng sidecar>`. `on_navigation` chỉ hủy điều hướng,
//!   không chặn mạng (WebView2 vẫn có thể gửi request đi), nên lớp bảo vệ token là bộ lọc header theo đúng cổng.
//! - Sidecar nằm trong Job Object `KILL_ON_JOB_CLOSE`: app thoát hay bị kill cứng ⇒ cả cây sidecar chết. Đóng cửa sổ
//!   chính cũng kill sidecar. Tắt êm (chạy `lifespan`) chưa làm.
//! - Chỉ một cửa sổ: lần mở thứ hai focus cửa sổ cũ (`tauri-plugin-single-instance`).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

const MAIN_WINDOW: &str = "main";
const TOKEN_HEADER: &str = "X-Holocron-Token";
const TOKEN_ENV: &str = "HOLOCRON_REQUIRE_TOKEN";
const PORT_PREFIX: &str = "Holocron: http://127.0.0.1:";
const PORT_TIMEOUT: Duration = Duration::from_secs(60);
const READY_TIMEOUT: Duration = Duration::from_secs(60);
const PROBE_INTERVAL: Duration = Duration::from_millis(50);
/// WebView2 của lần chạy trước (bị kill cứng) có thể còn giữ hồ sơ một lúc ⇒ tạo cửa sổ lỗi 0x800700AA; thử lại.
const WINDOW_RETRY_FOR: Duration = Duration::from_secs(30);
const WINDOW_RETRY_EVERY: Duration = Duration::from_millis(500);
const LOG_HINT: &str = "Xem log ở %LOCALAPPDATA%\\Holocron\\logs\\holocron.log.";

/// Biến môi trường được chép sang sidecar; mọi biến khác (kể cả key LLM/VLM) bị bỏ bằng `env_clear`.
const PASS_ENV: &[&str] = &[
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "HOLOCRON_TESSERACT_CMD",
    "HOLOCRON_TESSDATA_DIR",
];

struct Sidecar(Mutex<Option<Child>>);

enum LaunchEvent {
    Port(u16),
    Closed,
}

/// Bỏ tiền tố `\\?\` mà `resource_dir` trả trên Windows (Python dựng đường dẫn từ `sys.executable`).
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy().to_string();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path,
    }
}

/// 32 byte ngẫu nhiên của hệ điều hành, dạng hex (64 ký tự).
fn new_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("không lấy được số ngẫu nhiên: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn sidecar_env(token: &str, data_dir: &Path, web_dir: &Path) -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = PASS_ENV
        .iter()
        .filter_map(|name| std::env::var(name).ok().map(|value| (name.to_string(), value)))
        .collect();
    env.push(("HOLOCRON_DATA_DIR".into(), data_dir.to_string_lossy().into_owned()));
    env.push(("HOLOCRON_WEB_DIR".into(), web_dir.to_string_lossy().into_owned()));
    env.push((TOKEN_ENV.into(), token.into()));
    env
}

#[cfg(windows)]
fn put_in_kill_on_close_job(child: &Child) -> Result<(), String> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    // SAFETY: các lời gọi Win32 nhận handle hợp lệ (job vừa tạo, handle tiến trình con còn sống do `Child` giữ) và
    // con trỏ tới struct đúng kích thước khai báo.
    unsafe {
        let job = CreateJobObjectW(None, windows::core::PCWSTR::null()).map_err(|e| e.to_string())?;
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .map_err(|e| e.to_string())?;
        AssignProcessToJobObject(job, HANDLE(child.as_raw_handle())).map_err(|e| e.to_string())?;
        // Không đóng handle của job: nó sống tới khi tiến trình này chết (kể cả bị kill), khi đó hệ điều hành đóng
        // handle và kill cả cây sidecar.
    }
    Ok(())
}

#[cfg(not(windows))]
fn put_in_kill_on_close_job(_child: &Child) -> Result<(), String> {
    Err("Job Object chỉ có trên Windows".into())
}

fn spawn_sidecar(portable: &Path, data_dir: &Path, token: &str) -> Result<Child, String> {
    let python = portable.join("python").join("python.exe");
    let web_dir = portable.join("web");
    let mut cmd = Command::new(&python);
    cmd.args(["-I", "-X", "utf8", "-m", "holocron_core.launcher", "--no-browser"])
        .env_clear()
        .envs(sidecar_env(token, data_dir, &web_dir))
        .current_dir(portable)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("không chạy được {}: {e}", python.display()))?;
    if let Err(e) = put_in_kill_on_close_job(&child) {
        // Không có job thì app bị kill cứng sẽ để sidecar mồ côi: không chạy tiếp.
        let _ = child.kill();
        let _ = child.wait();
        return Err(format!("không gán được tiến trình nền vào Job Object: {e}"));
    }
    Ok(child)
}

/// Lấy cổng từ dòng `Holocron: http://127.0.0.1:<cổng>/` của launcher.
fn parse_port(line: &str) -> Option<u16> {
    let rest = line.trim_start_matches('\u{feff}').strip_prefix(PORT_PREFIX)?;
    let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
    match digits.parse::<u16>() {
        Ok(port) if port != 0 && rest[digits.len()..].starts_with('/') => Some(port),
        _ => None,
    }
}

/// Đọc hết stdout/stderr của sidecar (không ghi ra đâu: launcher tự ghi log của nó) để pipe không đầy; báo cổng khi
/// gặp và báo `Closed` khi stdout đóng (sidecar thoát).
fn spawn_readers(child: &mut Child, tx: mpsc::Sender<LaunchEvent>) -> Result<(), String> {
    let stdout = child.stdout.take().ok_or("thiếu stdout của tiến trình nền")?;
    let stderr = child.stderr.take().ok_or("thiếu stderr của tiến trình nền")?;
    std::thread::spawn(move || {
        let mut sent = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !sent {
                if let Some(port) = parse_port(&line) {
                    sent = true;
                    let _ = tx.send(LaunchEvent::Port(port));
                }
            }
        }
        let _ = tx.send(LaunchEvent::Closed);
    });
    std::thread::spawn(move || {
        let mut sink = Vec::new();
        let _ = BufReader::new(stderr).read_to_end(&mut sink);
    });
    Ok(())
}

/// `GET /v1/runtime` (kèm token nếu có); trả mã trạng thái HTTP, `None` khi chưa kết nối được.
fn probe_runtime(port: u16, token: Option<&str>) -> Option<u16> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok()?;
    let token_line = token.map(|t| format!("{TOKEN_HEADER}: {t}\r\n")).unwrap_or_default();
    let request =
        format!("GET /v1/runtime HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{token_line}Connection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).ok()?;
    let mut head = [0u8; 32];
    let n = stream.read(&mut head).ok()?;
    let text = String::from_utf8_lossy(&head[..n]);
    let mut parts = text.split_whitespace();
    if !parts.next()?.starts_with("HTTP/") {
        return None;
    }
    parts.next()?.parse().ok()
}

/// Đăng ký `WebResourceRequested` cho đúng `http://127.0.0.1:<port>/*`: gắn header token ở tầng native. Chờ tới khi
/// đăng ký xong (chạy trên luồng UI) rồi mới trả, để điều hướng sau đó chắc chắn mang token.
#[cfg(windows)]
fn attach_token_header(window: &WebviewWindow, token: String, port: u16) -> Result<(), String> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2WebResourceRequestedEventArgs, COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
    };
    use webview2_com::WebResourceRequestedEventHandler;
    use windows_core::{HSTRING, PCWSTR};

    let (tx, rx) = mpsc::channel::<Result<(), String>>();
    window
        .with_webview(move |platform| {
            // SAFETY: gọi COM của WebView2 trên luồng UI (with_webview chạy closure ở đó); các HSTRING sống trong
            // closure của handler suốt đời handler.
            let result = unsafe {
                (|| -> windows_core::Result<()> {
                    let core = platform.controller().CoreWebView2()?;
                    let filter = HSTRING::from(format!("http://127.0.0.1:{port}/*"));
                    core.AddWebResourceRequestedFilter(PCWSTR(filter.as_ptr()), COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL)?;
                    let name = HSTRING::from(TOKEN_HEADER);
                    let value = HSTRING::from(token.as_str());
                    let handler = WebResourceRequestedEventHandler::create(Box::new(
                        move |_sender, args: Option<ICoreWebView2WebResourceRequestedEventArgs>| {
                            if let Some(args) = args {
                                let headers = args.Request()?.Headers()?;
                                headers.SetHeader(PCWSTR(name.as_ptr()), PCWSTR(value.as_ptr()))?;
                            }
                            Ok(())
                        },
                    ));
                    let mut registration = 0i64;
                    core.add_WebResourceRequested(&handler, &mut registration)?;
                    Ok(())
                })()
            };
            let _ = tx.send(result.map_err(|e| e.to_string()));
        })
        .map_err(|e| e.to_string())?;
    rx.recv_timeout(Duration::from_secs(10))
        .map_err(|_| "WebView2 không phản hồi khi đăng ký header".to_string())?
}

#[cfg(not(windows))]
fn attach_token_header(_window: &WebviewWindow, _token: String, _port: u16) -> Result<(), String> {
    Err("bản desktop hiện chỉ hỗ trợ Windows (WebView2)".into())
}

/// Chuỗi JS an toàn cho mọi ký tự: mọi thứ ngoài chữ/số ASCII và dấu cách đổi thành `\uXXXX`.
fn js_string(text: &str) -> String {
    let mut out = String::from("\"");
    for unit in text.encode_utf16() {
        let c = char::from_u32(u32::from(unit)).filter(|c| c.is_ascii_alphanumeric() || *c == ' ');
        match c {
            Some(c) => out.push(c),
            None => out.push_str(&format!("\\u{unit:04x}")),
        }
    }
    out.push('"');
    out
}

/// Hiện lỗi trên trang chờ trong app (không bao giờ chứa token).
fn show_error(app: &AppHandle, title: &str, detail: &str) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let script = format!(
            "(function(){{var m=document.getElementById('status');if(!m)return;m.dataset.state='error';\
             document.getElementById('title').textContent={};document.getElementById('detail').textContent={};}})();",
            js_string(title),
            js_string(detail)
        );
        let _ = window.eval(&script);
    }
}

fn build_main_window(app: &AppHandle, allowed_port: Arc<AtomicU16>) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, MAIN_WINDOW, WebviewUrl::App("index.html".into()))
        .title("Holocron")
        .inner_size(1200.0, 760.0)
        .min_inner_size(800.0, 560.0)
        .center()
        .on_navigation(move |url: &Url| is_allowed_navigation(url, allowed_port.load(Ordering::SeqCst)))
        .build()
}

/// Chỉ trang chờ đóng gói trong app và đúng `http://127.0.0.1:<port>` của sidecar (port = 0: chưa sẵn sàng).
fn is_allowed_navigation(url: &Url, port: u16) -> bool {
    match url.scheme() {
        "tauri" => true,
        "http" | "https" if url.host_str() == Some("tauri.localhost") => true,
        "http" => port != 0 && url.host_str() == Some("127.0.0.1") && url.port() == Some(port),
        _ => false,
    }
}

/// Tạo cửa sổ chính; lỗi (thường do WebView2 của lần chạy trước còn giữ hồ sơ) ⇒ thử lại trong `WINDOW_RETRY_FOR`.
fn create_main_window(app: &AppHandle, allowed_port: Arc<AtomicU16>) -> Result<WebviewWindow, String> {
    let start = Instant::now();
    loop {
        match build_main_window(app, allowed_port.clone()) {
            Ok(window) => return Ok(window),
            Err(_) if start.elapsed() < WINDOW_RETRY_FOR => {
                if let Some(stale) = app.get_webview_window(MAIN_WINDOW) {
                    let _ = stale.destroy();
                }
                std::thread::sleep(WINDOW_RETRY_EVERY);
            }
            Err(e) => return Err(format!("không tạo được cửa sổ WebView2: {e}")),
        }
    }
}

/// Chờ cổng, kiểm thiếu token ⇒ 403, chờ `/v1/runtime` 200, gắn header rồi mới điều hướng tới backend.
fn wait_and_open(
    app: AppHandle,
    rx: mpsc::Receiver<LaunchEvent>,
    token: String,
    allowed_port: Arc<AtomicU16>,
) -> Result<(), (String, String)> {
    let fail = |title: &str, detail: String| (title.to_string(), detail);
    let port = match rx.recv_timeout(PORT_TIMEOUT) {
        Ok(LaunchEvent::Port(port)) => port,
        Ok(LaunchEvent::Closed) => {
            return Err(fail(
                "Tiến trình nền của Holocron đã thoát khi khởi động.",
                LOG_HINT.into(),
            ));
        }
        Err(_) => {
            return Err(fail(
                "Tiến trình nền của Holocron không khởi động kịp.",
                LOG_HINT.into(),
            ))
        }
    };
    let start = Instant::now();
    loop {
        if probe_runtime(port, Some(&token)) == Some(200) {
            break;
        }
        if matches!(rx.try_recv(), Ok(LaunchEvent::Closed)) {
            return Err(fail(
                "Tiến trình nền của Holocron đã thoát khi khởi động.",
                LOG_HINT.into(),
            ));
        }
        if start.elapsed() > READY_TIMEOUT {
            return Err(fail("Holocron không sẵn sàng sau 60 giây.", LOG_HINT.into()));
        }
        std::thread::sleep(PROBE_INTERVAL);
    }
    // Tự kiểm: request không token phải bị từ chối, nếu không thì không mở giao diện.
    let unauthenticated = probe_runtime(port, None);
    if unauthenticated != Some(403) {
        return Err(fail(
            "Kiểm tra bảo mật thất bại: máy chủ nền nhận request không có token.",
            format!("Mã trả về: {unauthenticated:?}. {LOG_HINT}"),
        ));
    }
    let window = app
        .get_webview_window(MAIN_WINDOW)
        .ok_or_else(|| fail("Không tìm thấy cửa sổ chính.", String::new()))?;
    attach_token_header(&window, token, port).map_err(|e| fail("Không gắn được token vào WebView2.", e))?;
    allowed_port.store(port, Ordering::SeqCst);
    let url: Url = format!("http://127.0.0.1:{port}/")
        .parse()
        .map_err(|e| fail("Địa chỉ máy chủ nền không hợp lệ.", format!("{e}")))?;
    window
        .navigate(url)
        .map_err(|e| fail("Không mở được giao diện.", e.to_string()))?;
    Ok(())
}

fn kill_sidecar(app: &AppHandle) {
    let child = app.state::<Sidecar>().0.lock().ok().and_then(|mut guard| guard.take());
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();
    let allowed_port = Arc::new(AtomicU16::new(0));
    let window = create_main_window(&handle, allowed_port.clone())?;
    let close_handle = handle.clone();
    window.on_window_event(move |event| {
        if matches!(event, WindowEvent::Destroyed) {
            kill_sidecar(&close_handle);
        }
    });

    let token = new_token()?;
    let portable = strip_verbatim(app.path().resource_dir()?).join("portable");
    let data_dir = strip_verbatim(app.path().app_local_data_dir()?).join("data");
    std::fs::create_dir_all(&data_dir)?;

    let mut child = match spawn_sidecar(&portable, &data_dir, &token) {
        Ok(child) => child,
        Err(e) => {
            show_error(&handle, "Không khởi động được tiến trình nền của Holocron.", &e);
            return Ok(());
        }
    };
    let (tx, rx) = mpsc::channel();
    let readers = spawn_readers(&mut child, tx);
    *app.state::<Sidecar>()
        .0
        .lock()
        .map_err(|_| "khoá trạng thái sidecar hỏng")? = Some(child);
    if let Err(e) = readers {
        show_error(&handle, "Không đọc được đầu ra của tiến trình nền.", &e);
        return Ok(());
    }

    std::thread::spawn(move || {
        if let Err((title, detail)) = wait_and_open(handle.clone(), rx, token, allowed_port) {
            show_error(&handle, &title, &detail);
        }
    });
    Ok(())
}

fn focus_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn main() {
    let app = tauri::Builder::default()
        // Đăng ký đầu tiên: lần mở thứ hai thoát ngay trong plugin, trước `setup` (không sinh token hay sidecar).
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            focus_main_window(app)
        }))
        .manage(Sidecar(Mutex::new(None)))
        .setup(setup)
        .build(tauri::generate_context!())
        .expect("không dựng được ứng dụng Holocron");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            kill_sidecar(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_port_reads_launcher_line() {
        assert_eq!(parse_port("Holocron: http://127.0.0.1:51234/"), Some(51234));
        assert_eq!(parse_port("\u{feff}Holocron: http://127.0.0.1:8000/"), Some(8000));
    }

    #[test]
    fn parse_port_rejects_other_lines() {
        assert_eq!(parse_port("Holocron đã chạy sẵn tại http://127.0.0.1:8000/"), None);
        assert_eq!(parse_port("Holocron: http://127.0.0.1:0/"), None);
        assert_eq!(parse_port("Holocron: http://127.0.0.1:99999/"), None);
        assert_eq!(parse_port("Holocron: http://127.0.0.1:80x/"), None);
    }

    #[test]
    fn navigation_only_to_sidecar_port() {
        let url = |s: &str| Url::parse(s).expect("url");
        assert!(is_allowed_navigation(&url("http://tauri.localhost/index.html"), 0));
        assert!(!is_allowed_navigation(&url("http://127.0.0.1:5000/"), 0));
        assert!(is_allowed_navigation(&url("http://127.0.0.1:5000/vault/"), 5000));
        assert!(!is_allowed_navigation(&url("http://127.0.0.1:5001/"), 5000));
        assert!(!is_allowed_navigation(&url("http://localhost:5000/"), 5000));
        assert!(!is_allowed_navigation(&url("https://127.0.0.1:5000/"), 5000));
        assert!(!is_allowed_navigation(&url("https://example.com/"), 5000));
        assert!(!is_allowed_navigation(&url("file:///C:/Windows/"), 5000));
    }

    #[test]
    fn sidecar_env_drops_secrets_and_sets_holocron_vars() {
        std::env::set_var("OPENAI_API_KEY", "khong-duoc-chep");
        let env = sidecar_env("abc", Path::new("D:/data"), Path::new("D:/web"));
        assert!(env.iter().all(|(k, _)| k != "OPENAI_API_KEY"));
        assert!(env
            .iter()
            .all(|(k, _)| PASS_ENV.contains(&k.as_str()) || k.starts_with("HOLOCRON_")));
        assert!(env.contains(&(TOKEN_ENV.to_string(), "abc".to_string())));
        assert!(env.contains(&("HOLOCRON_DATA_DIR".to_string(), "D:/data".to_string())));
        assert!(env.contains(&("HOLOCRON_WEB_DIR".to_string(), "D:/web".to_string())));
    }

    #[test]
    fn token_is_random_hex() {
        let a = new_token().expect("token");
        let b = new_token().expect("token");
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
    }

    #[test]
    fn js_string_escapes_everything_risky() {
        let js = js_string("a\"</script>\\ ồ\n");
        let inner = &js[1..js.len() - 1];
        assert!(!inner.contains(['"', '<', '>', '\n', '/']));
        assert!(inner
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == ' ' || c == '\\'));
        assert_eq!(js_string("Ab 1"), "\"Ab 1\"");
        assert_eq!(js_string("\""), "\"\\u0022\"");
    }
}
