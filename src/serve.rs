//! Built-in static file server (`__serve-static` internal subcommand).
//!
//! Replaces the Python version's `{python} -m http.server {port}` so the
//! deployed unit needs nothing but this binary. Single-threaded accept loop,
//! loopback-only bind, path traversal rejected.

use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::{Component, Path, PathBuf};

pub fn serve_static(root: &Path, port: u16) -> anyhow::Result<()> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port))?;
    eprintln!("serving {} on http://127.0.0.1:{port}", root.display());
    for stream in listener.incoming() {
        match stream {
            Ok(s) => handle(s, root),
            Err(e) => eprintln!("accept error: {e}"),
        }
    }
    Ok(())
}

fn handle(mut stream: TcpStream, root: &Path) {
    let mut buf = [0u8; 4096];
    let n = stream.read(&mut buf).unwrap_or(0);
    let req = String::from_utf8_lossy(&buf[..n]);
    let Some(path) = req.split_whitespace().nth(1) else {
        return respond(&mut stream, 400, "text/plain", b"bad request");
    };
    // Strip query/fragment.
    let path = path.split(['?', '#']).next().unwrap_or("/");
    let decoded = percent_decode(path);

    if !decoded.starts_with('/') {
        return respond(&mut stream, 400, "text/plain", b"bad request");
    }
    let rel: PathBuf = decoded.trim_start_matches('/').into();
    if rel.components().any(|c| matches!(c, Component::ParentDir)) {
        return respond(&mut stream, 403, "text/plain", b"forbidden");
    }

    let full = root.join(rel);
    let target = if full.is_dir() {
        match find_index(&full) {
            Some(i) => i,
            None => {
                let listing = dir_listing(root, &full);
                return respond(&mut stream, 200, "text/html", listing.as_bytes());
            }
        }
    } else {
        full
    };

    match std::fs::read(&target) {
        Ok(bytes) => {
            let mime = mime_of(&target);
            respond(&mut stream, 200, mime, &bytes)
        }
        Err(_) => respond(&mut stream, 404, "text/plain", b"not found"),
    }
}

fn find_index(dir: &Path) -> Option<PathBuf> {
    ["index.html", "index.htm"]
        .iter()
        .map(|i| dir.join(i))
        .find(|p| p.is_file())
}

fn dir_listing(root: &Path, dir: &Path) -> String {
    let entries = std::fs::read_dir(dir)
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    let suffix = if e.path().is_dir() { "/" } else { "" };
                    format!("<li><a href=\"{name}{suffix}\">{name}{suffix}</a></li>")
                })
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    let rel = dir
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    format!(
        "<!doctype html><meta charset=\"utf-8\"><title>{rel}</title>\
         <h1>Directory listing for /{rel}</h1><hr><ul>\n{entries}\n</ul>"
    )
}

fn respond(stream: &mut TcpStream, code: u16, mime: &str, body: &[u8]) {
    let reason = match code {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        _ => "Error",
    };
    let head = format!(
        "HTTP/1.0 {code} {reason}\r\nContent-Type: {mime}\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body);
}

fn percent_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() + 1 && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok();
            if let Some(v) = hex.and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn mime_of(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "html" | "htm" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "json" => "application/json",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "svg" => "image/svg+xml",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "txt" | "md" => "text/plain; charset=utf-8",
        "pdf" => "application/pdf",
        _ => "application/octet-stream",
    }
}
