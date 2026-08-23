//! Interactive panel — ratatui port of the Textual UI.
//!
//! Screen flow mirrors the Python version:
//! Main menu → System Scan / Deploy (URL input) / My Services.
//! Long operations run on worker threads reporting through a channel.
//!
//! Visual language: neon-on-black maxicolor theme. Every data type owns a
//! hue (cyan chrome · magenta deploy · green success · amber input/warnings
//! · red errors · violet scan sections). The event loop ticks ~80ms; the
//! title gradient and busy spinners are driven off that tick.

mod workers;

use std::io::{self, Stdout};
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

use crossterm::ExecutableCommand;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::terminal::{
    EnterAlternateScreen, LeaveAlternateScreen, disable_raw_mode, enable_raw_mode,
};
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph, Wrap};

// --- palette (truecolor; terminals without RGB get the nearest ANSI match) ---
pub(crate) const ACCENT: Color = Color::Rgb(64, 220, 255); // electric cyan — chrome/menu
pub(crate) const MAGENTA: Color = Color::Rgb(255, 92, 200); // deploy screen theme
pub(crate) const OK_GREEN: Color = Color::Rgb(80, 250, 123); // success / active units
pub(crate) const WARN_YELLOW: Color = Color::Rgb(255, 184, 0); // input / warnings
pub(crate) const ERR_RED: Color = Color::Rgb(255, 85, 85); // failures
pub(crate) const VIOLET: Color = Color::Rgb(171, 130, 255); // scan sections
pub(crate) const BLUE: Color = Color::Rgb(97, 143, 255); // services screen theme
const DIM: Color = Color::Rgb(110, 118, 129); // secondary text
const BODY: Color = Color::Rgb(205, 213, 224); // regular text
const BODY_ALT: Color = Color::Rgb(150, 160, 174); // zebra rows (no background)

/// Gradient ramp used by the animated title and spinners.
const RAMP: [Color; 3] = [ACCENT, MAGENTA, WARN_YELLOW];

const SPINNER_FRAMES: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

pub(crate) enum Screen {
    Main {
        selected: usize,
    },
    Scan {
        report: Option<String>,
        running: bool,
    },
    UrlInput {
        buffer: String,
        error: Option<String>,
        busy: bool,
    },
    Deploy {
        lines: Vec<String>,
        done: Option<bool>,
    },
    Services {
        rows: Vec<(String, String, String)>,
        selected: usize,
        message: Option<String>,
    },
}

pub(crate) enum Msg {
    Log(String),
    ScanDone(String),
    DeployDone(bool),
}

pub(crate) struct App {
    pub screen: Screen,
    pub rx: Receiver<Msg>,
    pub tx: Sender<Msg>,
    /// Drives animations; incremented every loop iteration (~80ms).
    pub tick: u64,
}

/// Entry point: sets up the terminal, runs the loop, restores the terminal.
pub fn run() -> anyhow::Result<()> {
    enable_raw_mode()?;
    io::stdout().execute(EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    terminal.clear()?;

    let (tx, rx) = mpsc::channel();
    let mut app = App {
        screen: Screen::Main { selected: 0 },
        rx,
        tx,
        tick: 0,
    };
    let res = event_loop(&mut terminal, &mut app);

    disable_raw_mode()?;
    io::stdout().execute(LeaveAlternateScreen)?;
    res
}

fn event_loop(
    terminal: &mut Terminal<CrosstermBackend<Stdout>>,
    app: &mut App,
) -> anyhow::Result<()> {
    loop {
        while let Ok(msg) = app.rx.try_recv() {
            match msg {
                Msg::ScanDone(report) => {
                    if let Screen::Scan { report: r, running } = &mut app.screen {
                        *r = Some(report);
                        *running = false;
                    }
                }
                Msg::Log(line) => {
                    if let Screen::Deploy { lines, .. } = &mut app.screen {
                        for l in line.split('\n') {
                            lines.push(l.to_string());
                        }
                    }
                }
                Msg::DeployDone(ok) => {
                    if let Screen::Deploy { lines, done } = &mut app.screen {
                        lines.push(if ok {
                            "deployment complete — service is up".into()
                        } else {
                            "deployment failed".into()
                        });
                        *done = Some(ok);
                    }
                }
            }
        }

        terminal.draw(|f| draw(f, app))?;
        app.tick = app.tick.wrapping_add(1);

        if event::poll(Duration::from_millis(80))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    let before = std::mem::discriminant(&app.screen);
                    match on_key(app, key.code, key.modifiers) {
                        Flow::Continue => {}
                        Flow::Exit => return Ok(()),
                    }
                    // Screen switched: ratatui's cell-diff would keep glyphs
                    // from the longer previous screen — force a full repaint.
                    if std::mem::discriminant(&app.screen) != before {
                        terminal.clear()?;
                    }
                }
            }
        }
    }
}

enum Flow {
    Continue,
    Exit,
}

fn on_key(app: &mut App, key: KeyCode, mods: KeyModifiers) -> Flow {
    if matches!(key, KeyCode::Char('c')) && mods.contains(KeyModifiers::CONTROL) {
        return Flow::Exit;
    }
    match &mut app.screen {
        Screen::Main { selected } => match key {
            KeyCode::Up | KeyCode::Char('k') => *selected = selected.saturating_sub(1),
            KeyCode::Down | KeyCode::Char('j') => *selected = (*selected + 1).min(2),
            KeyCode::Enter | KeyCode::Char(' ') => match selected {
                0 => {
                    workers::spawn_scan(app.tx.clone());
                    app.screen = Screen::Scan {
                        report: None,
                        running: true,
                    };
                }
                1 => {
                    app.screen = Screen::UrlInput {
                        buffer: String::new(),
                        error: None,
                        busy: false,
                    }
                }
                _ => {
                    let rows = workers::service_rows();
                    app.screen = Screen::Services {
                        rows,
                        selected: 0,
                        message: None,
                    };
                }
            },
            KeyCode::Char('q') | KeyCode::Esc => return Flow::Exit,
            _ => {}
        },
        Screen::Scan { .. } => {
            if matches!(key, KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q')) {
                app.screen = Screen::Main { selected: 0 };
            }
        }
        Screen::UrlInput {
            buffer,
            error,
            busy,
        } => {
            if *busy {
                return Flow::Continue;
            }
            match key {
                KeyCode::Esc => app.screen = Screen::Main { selected: 1 },
                KeyCode::Backspace => {
                    buffer.pop();
                }
                KeyCode::Enter => {
                    if buffer.trim().is_empty() {
                        *error = Some("enter a GitHub URL".into());
                    } else {
                        workers::start_deployment(app.tx.clone(), buffer.trim().to_string());
                        app.screen = Screen::Deploy {
                            lines: vec![format!("deploying {}...", buffer.trim())],
                            done: None,
                        };
                    }
                }
                KeyCode::Char(c) => buffer.push(c),
                _ => {}
            }
        }
        Screen::Deploy { .. } => {
            let finished = matches!(app.screen, Screen::Deploy { done: Some(_), .. });
            if finished && matches!(key, KeyCode::Enter | KeyCode::Esc | KeyCode::Char('q')) {
                app.screen = Screen::Main { selected: 1 };
            }
        }
        Screen::Services {
            rows,
            selected,
            message,
        } => {
            let _ = message; // status messages surface via refresh below
            let count = rows.len();
            match key {
                KeyCode::Esc | KeyCode::Char('q') => app.screen = Screen::Main { selected: 2 },
                KeyCode::Up | KeyCode::Char('k') if count > 0 => {
                    *selected = selected.saturating_sub(1)
                }
                KeyCode::Down | KeyCode::Char('j') if count > 0 => {
                    *selected = (*selected + 1).min(count - 1);
                }
                KeyCode::Char('r') => {
                    *rows = workers::service_rows();
                    *message = Some("refreshed".into());
                }
                KeyCode::Char('s') | KeyCode::Char('t') | KeyCode::Char('d') if count > 0 => {
                    if let Some((name, _, _)) = rows.get(*selected) {
                        let name = name.clone();
                        let action = match key {
                            KeyCode::Char('s') => "stop",
                            KeyCode::Char('t') => "start",
                            _ => "delete",
                        };
                        let msg = workers::service_action(&name, action);
                        *rows = workers::service_rows();
                        *message = Some(msg);
                    }
                }
                _ => {}
            }
        }
    }
    Flow::Continue
}

fn draw(f: &mut ratatui::Frame, app: &App) {
    let area = f.area();
    let chunks = Layout::vertical([
        Constraint::Length(4),
        Constraint::Min(1),
        Constraint::Length(1),
    ])
    .split(area);

    draw_header(f, chunks[0]);

    // Force-clean the body area in THIS frame's buffer so the diff engine
    // emits overwrites for stale glyphs left by longer previous frames
    // (ratatui only rewrites cells it believes changed).
    {
        let buf = f.buffer_mut();
        for y in chunks[1].top()..chunks[1].bottom() {
            for x in chunks[1].left()..chunks[1].right() {
                if let Some(cell) = buf.cell_mut((x, y)) {
                    cell.reset();
                }
            }
        }
    }

    match &app.screen {
        Screen::Main { selected } => {
            draw_main_menu(f, chunks[1], *selected);
        }
        Screen::Scan { report, running } => {
            draw_scan(f, chunks[1], report.as_deref(), *running, app.tick);
        }
        Screen::UrlInput {
            buffer,
            error,
            busy,
        } => {
            draw_url_input(f, chunks[1], buffer, error.as_deref(), *busy, app.tick);
        }
        Screen::Deploy { lines, done } => {
            draw_deploy(f, chunks[1], lines, *done);
        }
        Screen::Services {
            rows,
            selected,
            message,
        } => {
            draw_services(f, chunks[1], rows, *selected, message.as_deref());
        }
    }

    f.render_widget(Paragraph::new(hint_line(&app.screen)), chunks[2]);
}

// --- header -----------------------------------------------------------------

fn draw_header(f: &mut ratatui::Frame, area: ratatui::prelude::Rect) {
    let top = vec![
        Span::from(" "),
        Span::styled(
            "GHOSTPROVIDER",
            Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
        ),
        Span::styled(" · demo panel", Style::default().fg(DIM)),
    ];

    let version = env!("CARGO_PKG_VERSION");
    let bottom = Line::from(vec![
        Span::styled(" self-hosting demo · local-only", Style::default().fg(DIM)),
        Span::styled(format!(" · v{version} "), Style::default().fg(BLUE)),
    ]);

    f.render_widget(
        Paragraph::new(vec![Line::from(top), bottom]).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(ACCENT)),
        ),
        area,
    );
}

// --- main menu --------------------------------------------------------------

fn draw_main_menu(f: &mut ratatui::Frame, area: ratatui::prelude::Rect, selected: usize) {
    let items = [
        (
            "◉",
            "System Scan",
            "probe interfaces, ports, fingerprints",
            ACCENT,
        ),
        (
            "▲",
            "Deploy Service",
            "deploy one of the three curated services",
            MAGENTA,
        ),
        ("☰", "My Services", "manage deployed units", OK_GREEN),
    ];
    let list = List::new(items.iter().enumerate().map(|(i, (icon, t, d, hue))| {
        let sel = i == selected;
        let marker = if sel { " ▶ " } else { "   " };
        ListItem::new(Line::from(vec![
            Span::styled(
                marker,
                Style::default().fg(*hue).add_modifier(if sel {
                    Modifier::BOLD
                } else {
                    Modifier::empty()
                }),
            ),
            Span::styled(format!("{icon} "), Style::default().fg(*hue)),
            Span::styled(
                format!("{t} "),
                Style::default()
                    .fg(if sel { Color::White } else { BODY })
                    .add_modifier(if sel {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
            ),
            Span::styled(
                format!("— {d}"),
                Style::default().fg(if sel { *hue } else { DIM }),
            ),
        ]))
    }))
    .block(block("Menu", ACCENT));
    f.render_widget(list, area);
}

// --- system scan ------------------------------------------------------------

fn draw_scan(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    report: Option<&str>,
    running: bool,
    tick: u64,
) {
    let mut lines: Vec<Line> = Vec::new();
    match (report, running) {
        (Some(r), _) => lines.extend(colorize_scan(r)),
        (None, true) => {
            lines.push(Line::from(vec![
                Span::styled(spinner_char(tick), spinner_style(tick)),
                Span::styled(" scanning local system...", Style::default().fg(DIM)),
            ]));
            lines.push(Line::from(Span::styled(
                "  (this probes listening ports and fingerprints HTTP services)",
                Style::default().fg(DIM),
            )));
        }
        (None, false) => {}
    }
    f.render_widget(
        Paragraph::new(lines)
            .wrap(Wrap { trim: false })
            .block(block("System Scan", VIOLET)),
        area,
    );
}

fn spinner_char(tick: u64) -> &'static str {
    SPINNER_FRAMES[(tick / 2) as usize % SPINNER_FRAMES.len()]
}

fn spinner_style(tick: u64) -> Style {
    Style::default()
        .fg(RAMP[(tick / 6) as usize % RAMP.len()])
        .add_modifier(Modifier::BOLD)
}

/// Heuristic colorizer for the analyzer report (see workers::spawn_scan).
fn colorize_scan(report: &str) -> Vec<Line<'static>> {
    let mut out = Vec::new();
    let mut in_ports = false;
    let mut in_ifaces = false;
    let mut zebra = 0usize;
    for raw in report.lines() {
        let line = raw.strip_prefix('\n').unwrap_or(raw);
        let trimmed = line.trim_start();

        if trimmed == "Interfaces:" || trimmed == "Listening ports:" {
            in_ports = trimmed == "Listening ports:";
            in_ifaces = trimmed == "Interfaces:";
            zebra = 0;
            out.push(Line::from(Span::styled(
                format!(" {line}"),
                Style::default().fg(VIOLET).add_modifier(Modifier::BOLD),
            )));
        } else if trimmed.starts_with("PORT ") {
            out.push(Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(DIM),
            )));
        } else if in_ports && !trimmed.is_empty() {
            // Zebra striping without background fills — alternate text shades.
            let body_fg = if zebra % 2 == 1 { BODY_ALT } else { BODY };
            zebra += 1;
            let mut spans = vec![Span::raw(" ")];
            if let Some((port, rest)) = trimmed.split_once(char::is_whitespace) {
                spans.push(Span::styled(
                    port.to_string(),
                    Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
                ));
                spans.push(Span::styled(
                    format!(" {rest}"),
                    Style::default().fg(body_fg),
                ));
            } else {
                spans.push(Span::styled(
                    trimmed.to_string(),
                    Style::default().fg(body_fg),
                ));
            }
            out.push(Line::from(spans));
        } else if in_ifaces && !trimmed.is_empty() && !trimmed.contains(':') {
            // "name [ip] status" — split by exact whitespace boundaries and
            // colorize the trailing state token; keep original column gaps.
            let (head, status) = match trimmed.rfind(char::is_whitespace) {
                Some(i) => (trimmed[..i].to_string(), trimmed[i + 1..].to_string()),
                None => (trimmed.to_string(), String::new()),
            };
            let (name, rest) = match head.find(char::is_whitespace) {
                Some(i) => (head[..i].to_string(), head[i..].to_string()),
                None => (head.clone(), String::new()),
            };
            let status_color = match status.as_str() {
                "up" => OK_GREEN,
                "down" => ERR_RED,
                _ => WARN_YELLOW,
            };
            let mut spans = vec![
                Span::raw("   "),
                Span::styled(name, Style::default().fg(BODY).add_modifier(Modifier::BOLD)),
            ];
            if !rest.is_empty() {
                spans.push(Span::styled(rest, Style::default().fg(DIM)));
            }
            if !status.is_empty() {
                spans.push(Span::styled(
                    format!(" {status}"),
                    Style::default().fg(status_color),
                ));
            }
            out.push(Line::from(spans));
        } else if trimmed.starts_with("[x]") || trimmed.starts_with("[ ]") {
            let ok = trimmed.starts_with("[x]");
            let (mark, mark_color, rest_color) = if ok {
                ("✓", OK_GREEN, BODY)
            } else {
                ("✗", ERR_RED, DIM)
            };
            out.push(Line::from(vec![
                Span::raw(" "),
                Span::styled(
                    mark.to_string(),
                    Style::default().fg(mark_color).add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    format!(" {}", &trimmed[3..]),
                    Style::default().fg(rest_color),
                ),
            ]));
        } else if trimmed.starts_with("!") {
            out.push(Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(ERR_RED).add_modifier(Modifier::BOLD),
            )));
        } else if trimmed.starts_with("VPN active:") {
            out.push(Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(WARN_YELLOW),
            )));
        } else if trimmed.contains("MISSING")
            || trimmed.contains("not installed")
            || trimmed.contains("offline")
            || trimmed.contains("missing")
        {
            out.push(Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(ERR_RED),
            )));
        } else {
            out.push(Line::from(Span::styled(
                line.to_string(),
                Style::default().fg(BODY),
            )));
        }
    }
    out
}

// --- url input --------------------------------------------------------------

fn draw_url_input(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    buffer: &str,
    error: Option<&str>,
    busy: bool,
    tick: u64,
) {
    let mut text = vec![
        Line::from(Span::styled(
            " Supported demo services:",
            Style::default().fg(DIM),
        )),
        Line::from(vec![
            Span::raw("  "),
            Span::styled("VERT-sh/VERT", Style::default().fg(ACCENT)),
            Span::styled(" · ", Style::default().fg(DIM)),
            Span::styled("searxng/searxng", Style::default().fg(WARN_YELLOW)),
            Span::styled(" · ", Style::default().fg(DIM)),
            Span::styled("usememos/memos", Style::default().fg(OK_GREEN)),
        ]),
        Line::from(""),
        Line::from(vec![
            Span::styled(" GitHub URL: ", Style::default().fg(WARN_YELLOW)),
            Span::styled(buffer.to_string(), Style::default().fg(Color::White)),
            cursor_span(tick),
        ]),
    ];

    if let Some(e) = error {
        text.push(Line::from(Span::styled(
            format!(" ✗ {e}"),
            Style::default().fg(ERR_RED),
        )));
    } else if !buffer.trim().is_empty() {
        let ok = buffer.trim().starts_with("https://");
        let (mark, msg, color) = if ok {
            ("✓", " looks like a GitHub URL", OK_GREEN)
        } else {
            (
                "△",
                " expected https://github.com/<owner>/<repo>",
                WARN_YELLOW,
            )
        };
        text.push(Line::from(Span::styled(
            format!(" {mark}{msg}"),
            Style::default().fg(color),
        )));
    }
    if busy {
        text.push(Line::from(vec![
            Span::styled(spinner_char(tick), spinner_style(tick)),
            Span::styled(" resolving...", Style::default().fg(WARN_YELLOW)),
        ]));
    }
    f.render_widget(
        Paragraph::new(text)
            .wrap(Wrap { trim: false })
            .block(block("Deploy Service", WARN_YELLOW)),
        area,
    );
}

/// Blinking terminal-style cursor block.
fn cursor_span(tick: u64) -> Span<'static> {
    if (tick / 4) % 2 == 0 {
        Span::styled("█", Style::default().fg(WARN_YELLOW))
    } else {
        Span::raw(" ")
    }
}

// --- deploy -----------------------------------------------------------------

fn draw_deploy(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    lines: &[String],
    done: Option<bool>,
) {
    let inner = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(done.map_or(0, |_| 1)),
    ])
    .split(area);
    let spans: Vec<Line> = lines.iter().map(|l| log_line(l)).collect();
    f.render_widget(
        Paragraph::new(spans)
            .wrap(Wrap { trim: false })
            .block(block("Deployment", MAGENTA)),
        inner[0],
    );
    if let Some(ok) = done {
        let (label, fg) = if ok {
            (" ✔ DEPLOYED ✔ ", OK_GREEN)
        } else {
            (" ✖ FAILED ✖ ", ERR_RED)
        };
        f.render_widget(
            Paragraph::new(Line::from(Span::styled(
                label,
                Style::default()
                    .fg(Color::Black)
                    .bg(fg)
                    .add_modifier(Modifier::BOLD),
            )))
            .alignment(ratatui::layout::Alignment::Center),
            inner[1],
        );
    }
}

/// Colorize one deployment log line by its shape.
fn log_line(line: &str) -> Line<'static> {
    let s = line.trim_start();
    let style = if s.starts_with('!') || s.contains("failed") || s.contains("ERROR") {
        Style::default().fg(ERR_RED)
    } else if let Some(url) = s.strip_prefix("listening on ") {
        return Line::from(vec![
            Span::styled(" ✔ listening on ", Style::default().fg(OK_GREEN)),
            Span::styled(
                url.to_string(),
                Style::default().fg(OK_GREEN).add_modifier(Modifier::BOLD),
            ),
        ]);
    } else if s.ends_with("...") {
        Style::default().fg(WARN_YELLOW)
    } else if s.starts_with("=>") {
        Style::default().fg(ACCENT)
    } else {
        Style::default().fg(BODY)
    };
    Line::from(Span::styled(format!(" {line}"), style))
}

// --- my services ------------------------------------------------------------

fn draw_services(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    rows: &[(String, String, String)],
    selected: usize,
    message: Option<&str>,
) {
    let mut title = Line::from(vec![
        Span::styled(" My Services ", Style::default().fg(BLUE)),
        Span::styled("[", Style::default().fg(DIM)),
        Span::styled("s", Style::default().fg(WARN_YELLOW)),
        Span::styled("]top ", Style::default().fg(DIM)),
        Span::styled("[", Style::default().fg(DIM)),
        Span::styled("t", Style::default().fg(OK_GREEN)),
        Span::styled("]art ", Style::default().fg(DIM)),
        Span::styled("[", Style::default().fg(DIM)),
        Span::styled("d", Style::default().fg(ERR_RED)),
        Span::styled("]elete ", Style::default().fg(DIM)),
        Span::styled("[", Style::default().fg(DIM)),
        Span::styled("r", Style::default().fg(ACCENT)),
        Span::styled("]efresh", Style::default().fg(DIM)),
    ]);
    if let Some(m) = message {
        title.push_span(Span::styled(
            format!("  — {m}"),
            Style::default().fg(WARN_YELLOW),
        ));
    }

    if rows.is_empty() {
        f.render_widget(
            Paragraph::new(vec![
                Line::from(Span::styled(
                    " No services deployed yet.",
                    Style::default().fg(DIM),
                )),
                Line::from(""),
                Line::from(Span::styled(
                    " Use “Deploy Service” from the menu.",
                    Style::default().fg(MAGENTA),
                )),
            ])
            .block(Block::default().borders(Borders::ALL).title(title)),
            area,
        );
        return;
    }

    let items: Vec<ListItem> = rows
        .iter()
        .enumerate()
        .map(|(i, (name, status, url))| {
            let sel = i == selected;
            // No background fills — selection is arrow + bold + white,
            // zebra alternates text shades only.
            let name_fg = if sel {
                Color::White
            } else if i % 2 == 1 {
                BODY_ALT
            } else {
                BODY
            };
            let url_fg = DIM;
            let status_color = match status.as_str() {
                "active" => OK_GREEN,
                "activating" | "reloading" => WARN_YELLOW,
                _ => ERR_RED,
            };
            ListItem::new(Line::from(vec![
                Span::styled(
                    if sel { " ▶ " } else { "   " },
                    Style::default().fg(ACCENT).add_modifier(if sel {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
                ),
                Span::styled(
                    format!("{:<16}", name),
                    Style::default().fg(name_fg).add_modifier(if sel {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
                ),
                format!("{:<12}", "").into(),
                Span::styled("● ", Style::default().fg(status_color)),
                Span::styled(status.clone(), Style::default().fg(status_color)),
                Span::styled(format!("  {url}"), Style::default().fg(url_fg)),
            ]))
        })
        .collect();
    f.render_widget(
        List::new(items).block(Block::default().borders(Borders::ALL).title(title)),
        area,
    );
}

// --- hint bar ---------------------------------------------------------------

fn hint_line(screen: &Screen) -> Line<'static> {
    let pairs: Vec<(&str, Color)> = match screen {
        Screen::Main { .. } => vec![
            ("↑↓ select", ACCENT),
            ("Enter choose", OK_GREEN),
            ("q quit", MAGENTA),
        ],
        Screen::Scan { .. } => vec![("Esc back", VIOLET)],
        Screen::UrlInput { busy, .. } => {
            if *busy {
                vec![("working…", WARN_YELLOW)]
            } else {
                vec![("Enter deploy", OK_GREEN), ("Esc back", WARN_YELLOW)]
            }
        }
        Screen::Deploy { done, .. } => {
            if done.is_some() {
                vec![("Enter back", MAGENTA)]
            } else {
                vec![("working…", WARN_YELLOW)]
            }
        }
        Screen::Services { .. } => vec![
            ("↑↓ select", BLUE),
            ("s/t/d act", BLUE),
            ("r refresh", ACCENT),
            ("Esc back", BLUE),
        ],
    };
    let mut spans: Vec<Span> = Vec::new();
    for (i, (text, color)) in pairs.iter().enumerate() {
        if i > 0 {
            spans.push(Span::styled(" · ", Style::default().fg(DIM)));
        }
        spans.push(Span::styled(
            format!(" {text}"),
            Style::default().fg(*color),
        ));
    }
    Line::from(spans)
}

fn block(title: &str, color: Color) -> Block<'_> {
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(color))
        .title(Span::styled(
            format!(" {title} "),
            Style::default().fg(color).add_modifier(Modifier::BOLD),
        ))
}
