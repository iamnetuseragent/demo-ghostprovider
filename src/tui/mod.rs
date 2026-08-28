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
        /// Generation of the scan this screen is waiting for. A late
        /// ScanDone from an abandoned run (user left and re-entered) carries
        /// a stale seq and is dropped instead of overwriting fresh data.
        seq: u64,
    },
    UrlInput {
        buffer: String,
        error: Option<String>,
        busy: bool,
    },
    /// YES/NO gate before hosting a service (see README "Security model").
    Confirm {
        url: String,
        service_label: String,
        /// Which button the arrow keys currently highlight.
        yes_selected: bool,
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
    /// YES/NO gate before removing a service (unit, clone, caches).
    ConfirmDelete {
        name: String,
        /// Which button the arrow keys currently highlight (defaults to No).
        yes_selected: bool,
    },
}

pub(crate) enum Msg {
    Log(String),
    ScanDone(u64, String),
    DeployDone(bool),
}

pub(crate) struct App {
    pub screen: Screen,
    pub rx: Receiver<Msg>,
    pub tx: Sender<Msg>,
    /// Drives animations; incremented every loop iteration (~80ms).
    pub tick: u64,
    /// Incremented on every System Scan request; identifies the run.
    pub scan_seq: u64,
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
        scan_seq: 0,
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
                Msg::ScanDone(seq, report) => {
                    if let Screen::Scan {
                        report: r,
                        running,
                        seq: cur,
                    } = &mut app.screen
                    {
                        if *cur == seq {
                            *r = Some(report);
                            *running = false;
                        }
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

        // Self-heal: any stray glyph left by terminal desync (resize races,
        // tmux quirks, font-width lies) must not outlive ~2s — force a full
        // repaint periodically instead of trusting cell-diff forever.
        if app.tick % 25 == 0 {
            terminal.clear()?;
        }
        terminal.draw(|f| draw(f, app))?;
        app.tick = app.tick.wrapping_add(1);

        if event::poll(Duration::from_millis(80))? {
            match event::read()? {
                Event::Key(key) => {
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
                Event::Resize(_, _) => terminal.clear()?,
                _ => {}
            }
        }
    }
}

enum Flow {
    Continue,
    Exit,
}

/// Positional mapping national-layout → QWERTY for *command* keys only.
/// Free-text input (the URL field) always receives the raw character.
const LAYOUT_MAP: &[(char, char)] = &[
    // Russian ЙЦУКЕН
    ('й', 'q'),
    ('ц', 'w'),
    ('у', 'e'),
    ('к', 'r'),
    ('е', 't'),
    ('н', 'y'),
    ('г', 'u'),
    ('ш', 'i'),
    ('щ', 'o'),
    ('з', 'p'),
    ('ф', 'a'),
    ('ы', 's'),
    ('в', 'd'),
    ('а', 'f'),
    ('п', 'g'),
    ('р', 'h'),
    ('о', 'j'),
    ('л', 'k'),
    ('д', 'l'),
    ('я', 'z'),
    ('ч', 'x'),
    ('с', 'c'),
    ('м', 'v'),
    ('и', 'b'),
    ('т', 'n'),
    ('ь', 'm'),
];

/// Normalize a key event to the QWERTY command letter it represents,
/// regardless of the active keyboard layout. Uppercase is folded; ASCII
/// passes through unchanged. Characters without a positional counterpart
/// (digits, punctuation, unmapped letters) yield None so they never fire
/// a command by accident.
fn command_char(key: KeyCode) -> Option<char> {
    let c = match key {
        KeyCode::Char(c) => c,
        _ => return None,
    };
    let lower = c.to_lowercase().next().unwrap_or(c);
    if lower.is_ascii() {
        return Some(lower);
    }
    LAYOUT_MAP
        .iter()
        .find(|(nat, _)| *nat == lower)
        .map(|(_, lat)| *lat)
}

fn on_key(app: &mut App, key: KeyCode, mods: KeyModifiers) -> Flow {
    if matches!(key, KeyCode::Char('c')) && mods.contains(KeyModifiers::CONTROL) {
        return Flow::Exit;
    }
    // Main-menu activation touches app.tx and app.screen at once; decide it
    // before the screen match takes its borrow.
    if let Screen::Main { selected } = app.screen {
        if matches!(key, KeyCode::Enter | KeyCode::Char(' ')) {
            return main_menu_activate(app, selected);
        }
    }
    let cmd = command_char(key);
    match &mut app.screen {
        Screen::Main { selected } => match key {
            KeyCode::Up | KeyCode::Down => move_main_selection(selected, key),
            KeyCode::Enter | KeyCode::Char(' ') => {}
            KeyCode::Esc => return Flow::Exit,
            _ => match cmd {
                Some('k') => *selected = selected.saturating_sub(1),
                Some('j') => *selected = (*selected + 1).min(2),
                Some('q') => return Flow::Exit,
                _ => {}
            },
        },
        Screen::Scan { .. } => {
            if matches!(key, KeyCode::Esc | KeyCode::Enter) || cmd == Some('q') {
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
                        let url = buffer.trim().to_string();
                        let service_label = confirm_label(&url);
                        app.screen = Screen::Confirm {
                            url,
                            service_label,
                            yes_selected: true,
                        };
                    }
                }
                // Raw char: URLs must be typed verbatim on any layout.
                KeyCode::Char(c) => buffer.push(c),
                _ => {}
            }
        }
        Screen::Confirm {
            url, yes_selected, ..
        } => {
            let mut decision: Option<bool> = None;
            match key {
                KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down => {
                    *yes_selected = !*yes_selected;
                }
                KeyCode::Esc => decision = Some(false),
                // The ONLY committing key: Enter accepts the highlighted choice.
                KeyCode::Enter => decision = Some(*yes_selected),
                _ => match cmd {
                    // Letters only move the highlight; nothing runs without
                    // an explicit Enter.
                    Some('y') => *yes_selected = true,
                    Some('n') => *yes_selected = false,
                    _ => {}
                },
            }
            match decision {
                Some(true) => {
                    workers::start_deployment(app.tx.clone(), url.clone());
                    app.screen = Screen::Deploy {
                        lines: vec![format!("deploying {}...", url)],
                        done: None,
                    };
                }
                // Back to the input with the URL preserved.
                Some(false) => {
                    app.screen = Screen::UrlInput {
                        buffer: url.clone(),
                        error: None,
                        busy: false,
                    }
                }
                None => {}
            }
        }
        Screen::Deploy { .. } => {
            let finished = matches!(app.screen, Screen::Deploy { done: Some(_), .. });
            if finished && (matches!(key, KeyCode::Enter | KeyCode::Esc) || cmd == Some('q')) {
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
                KeyCode::Esc => app.screen = Screen::Main { selected: 2 },
                KeyCode::Up if count > 0 => *selected = selected.saturating_sub(1),
                KeyCode::Down if count > 0 => *selected = (*selected + 1).min(count - 1),
                _ => match cmd {
                    Some('q') => app.screen = Screen::Main { selected: 2 },
                    Some('k') if count > 0 => *selected = selected.saturating_sub(1),
                    Some('j') if count > 0 => *selected = (*selected + 1).min(count - 1),
                    Some('r') => {
                        *rows = workers::service_rows();
                        *message = Some("refreshed".into());
                    }
                    Some(action @ ('s' | 't' | 'd')) if count > 0 => {
                        if let Some((name, _, _)) = rows.get(*selected) {
                            let name = name.clone();
                            match action {
                                's' => {
                                    let msg = workers::service_action(&name, "stop");
                                    *rows = workers::service_rows();
                                    *message = Some(msg);
                                }
                                't' => {
                                    let msg = workers::service_action(&name, "start");
                                    *rows = workers::service_rows();
                                    *message = Some(msg);
                                }
                                // Deletion is destructive (unit + clone +
                                // caches): gate it behind an explicit YES/NO
                                // that defaults to No.
                                _ => {
                                    app.screen = Screen::ConfirmDelete {
                                        name,
                                        yes_selected: false,
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                },
            }
        }
        Screen::ConfirmDelete { name, yes_selected } => {
            let mut decision: Option<bool> = None;
            match key {
                KeyCode::Left | KeyCode::Right | KeyCode::Up | KeyCode::Down => {
                    *yes_selected = !*yes_selected;
                }
                KeyCode::Esc => decision = Some(false),
                // The ONLY committing key: Enter accepts the highlighted choice.
                KeyCode::Enter => decision = Some(*yes_selected),
                _ => match cmd {
                    // Letters only move the highlight; nothing runs without
                    // an explicit Enter.
                    Some('y') => *yes_selected = true,
                    Some('n') => *yes_selected = false,
                    _ => {}
                },
            }
            match decision {
                Some(true) => {
                    let msg = workers::service_action(name, "delete");
                    let rows = workers::service_rows();
                    app.screen = Screen::Services {
                        rows,
                        selected: 0,
                        message: Some(msg),
                    };
                }
                Some(false) => {
                    let rows = workers::service_rows();
                    app.screen = Screen::Services {
                        rows,
                        selected: 0,
                        message: None,
                    };
                }
                None => {}
            }
        }
    }
    Flow::Continue
}

fn move_main_selection(selected: &mut usize, key: KeyCode) {
    match key {
        KeyCode::Up => *selected = selected.saturating_sub(1),
        _ => *selected = (*selected + 1).min(2),
    }
}

fn main_menu_activate(app: &mut App, selected: usize) -> Flow {
    match selected {
        0 => {
            workers::spawn_scan(app.tx.clone(), app.scan_seq + 1);
            app.scan_seq += 1;
            app.screen = Screen::Scan {
                report: None,
                running: true,
                seq: app.scan_seq,
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
        Screen::Scan {
            report, running, ..
        } => {
            draw_scan(f, chunks[1], report.as_deref(), *running, app.tick);
        }
        Screen::UrlInput {
            buffer,
            error,
            busy,
        } => {
            draw_url_input(f, chunks[1], buffer, error.as_deref(), *busy, app.tick);
        }
        Screen::Confirm {
            url,
            service_label,
            yes_selected,
        } => {
            draw_confirm(f, chunks[1], url, service_label, *yes_selected);
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
        Screen::ConfirmDelete { name, yes_selected } => {
            draw_confirm_delete(f, chunks[1], name, *yes_selected);
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
            "@",
            "System Scan",
            "probe interfaces and occupied ports",
            ACCENT,
        ),
        (
            "^",
            "Deploy Service",
            "deploy one of the three curated services",
            MAGENTA,
        ),
        ("#", "My Services", "manage deployed units", OK_GREEN),
    ];
    let list = List::new(items.iter().enumerate().map(|(i, (icon, t, d, hue))| {
        let sel = i == selected;
        let marker = if sel { " » " } else { "   " };
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
                "  (this lists interfaces and occupied ports)",
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
    for raw in report.lines() {
        let line = raw.strip_prefix('\n').unwrap_or(raw);
        let trimmed = line.trim_start();

        if trimmed == "Interfaces:" || trimmed == "Listening ports:" {
            in_ports = trimmed == "Listening ports:";
            in_ifaces = trimmed == "Interfaces:";
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
            // Empty-section placeholder: dim, never styled like a port row.
            if trimmed == "(none)" {
                out.push(Line::from(Span::styled(
                    format!(" {line}"),
                    Style::default().fg(DIM),
                )));
                continue;
            }
            // Ports of our own deployments carry the "(deployed)" tag —
            // yellow bold. Every other row is an anonymous occupied port:
            // violet bold, with no owner attribution ever.
            let deployed = trimmed.contains("(deployed)");
            let hue = if deployed { WARN_YELLOW } else { VIOLET };
            let mut spans = vec![Span::raw(" ")];
            let (port, rest) = trimmed
                .split_once(char::is_whitespace)
                .unwrap_or((trimmed, ""));
            spans.push(Span::styled(
                port.to_string(),
                Style::default().fg(hue).add_modifier(Modifier::BOLD),
            ));
            if !rest.is_empty() {
                spans.push(Span::styled(
                    format!(" {rest}"),
                    Style::default().fg(hue).add_modifier(Modifier::BOLD),
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
                ("+", OK_GREEN, BODY)
            } else {
                ("x", ERR_RED, DIM)
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
            format!(" x {e}"),
            Style::default().fg(ERR_RED),
        )));
    } else if !buffer.trim().is_empty() {
        let ok = buffer.trim().starts_with("https://");
        let (mark, msg, color) = if ok {
            ("+", " looks like a GitHub URL", OK_GREEN)
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

// --- confirmation gates ------------------------------------------------------

/// Human-readable service label for the deploy confirmation screen; falls
/// back to the raw URL when it does not match a curated recipe.
fn confirm_label(url: &str) -> String {
    crate::hoster::github::parse_github_url(url)
        .and_then(|(owner, name)| {
            crate::hoster::recipes::find_recipe(&owner, &name).map(|r| r.display_name.to_string())
        })
        .unwrap_or_else(|| url.to_string())
}

/// One selectable YES/NO button; the arrow keys move the highlight.
fn choice_spans(marker: &str, label: &str, hue: Color, selected: bool) -> Vec<Span<'static>> {
    let mut spans = vec![Span::raw("   ")];
    if selected {
        spans.push(Span::styled(
            "» ".to_string(),
            Style::default().fg(hue).add_modifier(Modifier::BOLD),
        ));
        spans.push(Span::styled(
            marker.to_string(),
            Style::default()
                .fg(hue)
                .add_modifier(Modifier::BOLD | Modifier::REVERSED),
        ));
        spans.push(Span::styled(
            label.to_string(),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ));
    } else {
        spans.push(Span::raw("  "));
        spans.push(Span::styled(marker.to_string(), Style::default().fg(DIM)));
        spans.push(Span::styled(
            label.to_string(),
            Style::default().fg(BODY_ALT),
        ));
    }
    spans
}

fn draw_confirm(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    url: &str,
    service_label: &str,
    yes_selected: bool,
) {
    let text = vec![
        Line::from(""),
        Line::from(vec![
            Span::styled(
                " Host ",
                Style::default()
                    .fg(WARN_YELLOW)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(service_label.to_string(), Style::default().fg(Color::White)),
            Span::styled(" on this machine?", Style::default().fg(BODY)),
        ]),
        Line::from(vec![
            Span::styled(" URL: ", Style::default().fg(DIM)),
            Span::styled(url.to_string(), Style::default().fg(ACCENT)),
        ]),
        Line::from(""),
        Line::from(choice_spans("[Y]es", " — deploy", OK_GREEN, yes_selected)),
        Line::from(choice_spans(
            "[N]o",
            " — cancel",
            WARN_YELLOW,
            !yes_selected,
        )),
        Line::from(""),
        Line::from(Span::styled(
            " ←→ select · Enter confirm",
            Style::default().fg(DIM),
        )),
    ];
    f.render_widget(
        Paragraph::new(text)
            .wrap(Wrap { trim: false })
            .block(block("Confirm Deployment", WARN_YELLOW)),
        area,
    );
}

fn draw_confirm_delete(
    f: &mut ratatui::Frame,
    area: ratatui::prelude::Rect,
    name: &str,
    yes_selected: bool,
) {
    let text = vec![
        Line::from(""),
        Line::from(vec![
            Span::styled(
                " Remove ",
                Style::default().fg(ERR_RED).add_modifier(Modifier::BOLD),
            ),
            Span::styled(name.to_string(), Style::default().fg(Color::White)),
            Span::styled("?", Style::default().fg(ERR_RED)),
        ]),
        Line::from(Span::styled(
            " This deletes the unit, the cloned repository and its caches.",
            Style::default().fg(DIM),
        )),
        Line::from(""),
        Line::from(choice_spans("[Y]es", " — remove", ERR_RED, yes_selected)),
        Line::from(choice_spans("[N]o", " — keep", OK_GREEN, !yes_selected)),
        Line::from(""),
        Line::from(Span::styled(
            " ←→ select · Enter confirm",
            Style::default().fg(DIM),
        )),
    ];
    f.render_widget(
        Paragraph::new(text)
            .wrap(Wrap { trim: false })
            .block(block("Confirm Removal", ERR_RED)),
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
    let mut spans: Vec<Line> = Vec::new();
    let mut prev_doctor = false;
    for l in lines {
        let t = l.trim_start();
        let is_doctor = t.starts_with("! ") && doctor_body_markers(t);
        if is_doctor && !prev_doctor {
            if !spans.is_empty() {
                spans.push(Line::from(""));
            }
            spans.push(Line::from(vec![
                Span::styled(" ──", Style::default().fg(DIM)),
                Span::styled(
                    " TOOL DOCTOR ",
                    Style::default()
                        .fg(WARN_YELLOW)
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled("────────────────", Style::default().fg(DIM)),
            ]));
        }
        spans.extend(log_lines(l));
        prev_doctor = is_doctor;
    }
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

/// Colorize one deployment log line; tool-doctor issues get structured
/// multi-line treatment (problem / fix command / note).
fn log_lines(line: &str) -> Vec<Line<'static>> {
    let s = line.trim_start();
    if let Some(body) = s.strip_prefix("! ") {
        if doctor_body_markers(s) {
            return doctor_lines(body).expect("doctor marker verified above");
        }
        return vec![Line::from(vec![
            Span::styled(
                " ✖ ".to_string(),
                Style::default().fg(ERR_RED).add_modifier(Modifier::BOLD),
            ),
            Span::styled(body.to_string(), Style::default().fg(ERR_RED)),
        ])];
    }
    if let Some(body) = s.strip_prefix("warn: ") {
        return vec![Line::from(vec![
            Span::styled(
                " ! ".to_string(),
                Style::default()
                    .fg(WARN_YELLOW)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(body.to_string(), Style::default().fg(WARN_YELLOW)),
        ])];
    }
    if let Some(url) = s.strip_prefix("listening on ") {
        return vec![Line::from(vec![
            Span::styled(" ✔ listening on ", Style::default().fg(OK_GREEN)),
            Span::styled(
                url.to_string(),
                Style::default().fg(OK_GREEN).add_modifier(Modifier::BOLD),
            ),
        ])];
    }
    let style = if s.contains("failed") || s.contains("ERROR") {
        Style::default().fg(ERR_RED)
    } else if s.ends_with("...") {
        Style::default().fg(WARN_YELLOW)
    } else if s.starts_with("=>") {
        Style::default().fg(ACCENT)
    } else {
        Style::default().fg(BODY)
    };
    vec![Line::from(Span::styled(format!(" {line}"), style))]
}

/// A doctor issue carries a problem statement plus an explicit fix command.
fn doctor_body_markers(s: &str) -> bool {
    crate::hoster::toolcheck::is_issue_line(s)
}

fn doctor_lines(body: &str) -> Option<Vec<Line<'static>>> {
    let (problem, cmd, note) = crate::hoster::toolcheck::split_issue(body)?;
    let mut out = vec![
        Line::from(vec![
            Span::styled(
                " ! ".to_string(),
                Style::default()
                    .fg(WARN_YELLOW)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(problem.to_string(), Style::default().fg(ERR_RED)),
        ]),
        Line::from(vec![
            Span::styled("   fix » ".to_string(), Style::default().fg(DIM)),
            Span::styled(
                cmd.to_string(),
                Style::default().fg(ACCENT).add_modifier(Modifier::BOLD),
            ),
        ]),
    ];
    if let Some(n) = note {
        out.push(Line::from(vec![
            Span::styled("      ".to_string(), Style::default().fg(DIM)),
            Span::styled(n.to_string(), Style::default().fg(DIM)),
        ]));
    }
    Some(out)
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
                    if sel { " » " } else { "   " },
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
        Screen::Confirm { .. } => vec![
            ("←→ select", ACCENT),
            ("Y/N", OK_GREEN),
            ("Enter confirm", OK_GREEN),
            ("Esc cancel", WARN_YELLOW),
        ],
        Screen::ConfirmDelete { .. } => vec![
            ("←→ select", ACCENT),
            ("Y/N", ERR_RED),
            ("Enter confirm", OK_GREEN),
            ("Esc keep", OK_GREEN),
        ],
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Port rows: our deployments render yellow bold; anonymous occupied
    /// ports render violet bold — both without any owner data.
    #[test]
    fn scan_report_colors_deployed_yellow_and_foreign_violet() {
        let report = "Listening ports:\n  5432   \n  23920  demo-memos (deployed)\n";
        let lines = colorize_scan(report);

        // Line layout: header, then one line per port row.
        let foreign = &lines[1];
        let deployed = &lines[2];

        assert!(
            foreign
                .spans
                .iter()
                .skip(1) // leading indent span carries no color
                .all(
                    |s| s.style.fg == Some(VIOLET) && s.style.add_modifier.contains(Modifier::BOLD)
                ),
            "foreign occupied ports must be violet bold, got {:?}",
            foreign.spans.iter().map(|s| s.style.fg).collect::<Vec<_>>()
        );
        assert!(
            deployed
                .spans
                .iter()
                .skip(1)
                .all(|s| s.style.fg == Some(WARN_YELLOW)
                    && s.style.add_modifier.contains(Modifier::BOLD)),
            "deployed rows must be yellow bold, got {:?}",
            deployed
                .spans
                .iter()
                .map(|s| s.style.fg)
                .collect::<Vec<_>>()
        );
    }

    #[test]
    fn command_char_passes_latin_through_case_insensitive() {
        assert_eq!(command_char(KeyCode::Char('q')), Some('q'));
        assert_eq!(command_char(KeyCode::Char('Q')), Some('q'));
        assert_eq!(command_char(KeyCode::Char('Y')), Some('y'));
        assert_eq!(command_char(KeyCode::Char('D')), Some('d'));
        assert_eq!(command_char(KeyCode::Char(' ')), Some(' '));
        assert_eq!(command_char(KeyCode::Char('1')), Some('1'));
        // Non-char keys never map to a command letter.
        assert_eq!(command_char(KeyCode::Enter), None);
        assert_eq!(command_char(KeyCode::Esc), None);
    }

    /// Russian ЙЦУКЕН: the same physical keys must trigger the same
    /// commands as their QWERTY counterparts, upper or lower case.
    #[test]
    fn command_char_maps_cyrillic_positionally() {
        let cases = [
            ('й', "q"),
            ('Й', "q"), // quit
            ('л', "k"),
            ('Л', "k"), // up (vim-style)
            ('о', "j"),
            ('О', "j"), // down (vim-style)
            ('к', "r"), // refresh
            ('ы', "s"), // stop
            ('е', "t"), // start
            ('в', "d"), // delete
            ('н', "y"),
            ('Н', "y"), // yes
            ('т', "n"),
            ('Т', "n"), // no
        ];
        for (cyr, lat) in cases {
            assert_eq!(
                command_char(KeyCode::Char(cyr)),
                Some(lat.chars().next().unwrap()),
                "{cyr} must act as {lat}"
            );
        }
    }

    /// Letters without a positional counterpart never fire commands.
    #[test]
    fn command_char_leaves_unmapped_chars_alone() {
        assert_eq!(command_char(KeyCode::Char('б')), None);
        assert_eq!(command_char(KeyCode::Char('ю')), None);
        assert_eq!(command_char(KeyCode::Char('ж')), None);
        assert_eq!(command_char(KeyCode::Char('ё')), None);
    }

    #[test]
    fn confirm_label_maps_curated_repos_to_display_names() {
        assert_eq!(confirm_label("https://github.com/usememos/memos"), "Memos");
        assert_eq!(confirm_label("https://github.com/VERT-sh/VERT"), "VERT");
        assert_eq!(
            confirm_label("https://github.com/searxng/searxng"),
            "SearXNG"
        );
    }

    #[test]
    fn confirm_label_falls_back_to_raw_url() {
        assert_eq!(
            confirm_label("https://github.com/foo/bar"),
            "https://github.com/foo/bar"
        );
        assert_eq!(confirm_label("not a url"), "not a url");
    }

    #[test]
    fn doctor_line_is_split_into_problem_fix_note() {
        let src = "! Memos needs Go >= 1.27.0, found 1.26.6 — update first: sudo pacman -S --needed go  (this software never runs sudo commands by itself)";
        let body = src.trim_start().strip_prefix("! ").unwrap();
        let lines = doctor_lines(body).unwrap();
        assert_eq!(lines.len(), 3);
        assert!(lines[1].spans.iter().any(|s| s.content.contains("fix » ")));
        assert!(
            lines[1]
                .spans
                .iter()
                .any(|s| s.content == "sudo pacman -S --needed go")
        );
        assert!(lines[2].spans.iter().any(|s| s.content.starts_with('(')));
    }

    #[test]
    fn doctor_line_without_note_has_two_rows() {
        let lines = doctor_lines("VERT needs Bun >= 1.2.0 but 'bun' is not installed — install: curl -fsSL https://bun.sh/install | bash").unwrap();
        assert_eq!(lines.len(), 2);
    }

    #[test]
    fn marker_detection_matches_only_doctor_issues() {
        assert!(doctor_body_markers(
            "! X needs Go >= 1.27.0 — update first: cmd"
        ));
        assert!(!doctor_body_markers("! invalid GitHub URL format"));
    }
}
