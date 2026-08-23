//! Interactive panel — ratatui port of the Textual UI.
//!
//! Screen flow mirrors the Python version:
//! Main menu → System Scan / Deploy (URL input) / My Services.
//! Long operations run on worker threads reporting through a channel.

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

pub(crate) const ACCENT: Color = Color::LightCyan;
pub(crate) const OK_GREEN: Color = Color::LightGreen;
pub(crate) const WARN_YELLOW: Color = Color::Yellow;
pub(crate) const ERR_RED: Color = Color::Red;
pub(crate) const DIM: Color = Color::DarkGray;

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

        if event::poll(Duration::from_millis(80))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    match on_key(app, key.code, key.modifiers) {
                        Flow::Continue => {}
                        Flow::Exit => return Ok(()),
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
            let _ = rows;
            let _ = selected;
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
        Constraint::Length(3),
        Constraint::Min(1),
        Constraint::Length(1),
    ])
    .split(area);

    let title = Paragraph::new(" GHOSTPROVIDER · demo panel")
        .style(Style::default().fg(ACCENT).add_modifier(Modifier::BOLD))
        .block(Block::default().borders(Borders::ALL));
    f.render_widget(title, chunks[0]);

    match &app.screen {
        Screen::Main { selected } => {
            let items = [
                ("System Scan", "probe interfaces, ports, fingerprints"),
                ("Deploy Service", "deploy one of the three curated services"),
                ("My Services", "manage deployed units"),
            ];
            let list = List::new(items.iter().enumerate().map(|(i, (t, d))| {
                let style = if i == *selected {
                    Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
                } else {
                    Style::default()
                };
                ListItem::new(Line::from(vec![
                    Span::styled(format!(" {t} "), style),
                    Span::styled(format!("— {d}"), Style::default().fg(DIM)),
                ]))
            }))
            .block(block("Menu"));
            f.render_widget(list, chunks[1]);
        }
        Screen::Scan { report, running } => {
            let text = match (report, running) {
                (Some(r), _) => r.clone(),
                (None, true) => "scanning local system...\n(this probes listening ports and fingerprints HTTP services)".into(),
                (None, false) => String::new(),
            };
            f.render_widget(
                Paragraph::new(text)
                    .wrap(Wrap { trim: false })
                    .block(block("System Scan")),
                chunks[1],
            );
        }
        Screen::UrlInput {
            buffer,
            error,
            busy,
        } => {
            let mut text = vec![
                Line::from(" Supported demo services:"),
                Line::from(vec![Span::styled(
                    "  VERT-sh/VERT · searxng/searxng · usememos/memos",
                    Style::default().fg(DIM),
                )]),
                Line::from(""),
                Line::from(format!(" GitHub URL: {buffer}")),
            ];
            if let Some(e) = error {
                text.push(Line::from(Span::styled(
                    e.clone(),
                    Style::default().fg(ERR_RED),
                )));
            }
            if *busy {
                text.push(Line::from(Span::styled(
                    "resolving...",
                    Style::default().fg(WARN_YELLOW),
                )));
            }
            f.render_widget(
                Paragraph::new(text)
                    .wrap(Wrap { trim: false })
                    .block(block("Deploy Service")),
                chunks[1],
            );
        }
        Screen::Deploy { lines, .. } => {
            let spans: Vec<Line> = lines.iter().map(|l| log_line(l)).collect();
            f.render_widget(
                Paragraph::new(spans)
                    .wrap(Wrap { trim: false })
                    .block(block("Deployment")),
                chunks[1],
            );
        }
        Screen::Services {
            rows,
            selected,
            message,
        } => {
            let title = match message {
                Some(m) => format!("My Services  [s]top [t]art [d]elete [r]efresh  — {m}"),
                None => "My Services  [s]top [t]art [d]elete [r]efresh".to_string(),
            };
            if rows.is_empty() {
                f.render_widget(
                    Paragraph::new(
                        "No services deployed yet.\n\nUse “Deploy Service” from the menu.",
                    )
                    .block(block(&title)),
                    chunks[1],
                );
            } else {
                let items: Vec<ListItem> = rows
                    .iter()
                    .enumerate()
                    .map(|(i, (name, status, url))| {
                        let sel = i == *selected;
                        let status_span = match status.as_str() {
                            "active" => Span::styled(status.clone(), Style::default().fg(OK_GREEN)),
                            "activating" | "reloading" => {
                                Span::styled(status.clone(), Style::default().fg(WARN_YELLOW))
                            }
                            other => Span::styled(other.to_string(), Style::default().fg(ERR_RED)),
                        };
                        ListItem::new(Line::from(vec![
                            Span::styled(
                                if sel { " ▶ " } else { "   " },
                                Style::default().fg(ACCENT),
                            ),
                            Span::styled(
                                format!("{:<16}", name),
                                Style::default().add_modifier(if sel {
                                    Modifier::BOLD
                                } else {
                                    Modifier::empty()
                                }),
                            ),
                            format!("{:<12}", "").into(),
                            status_span,
                            Span::styled(format!("  {url}"), Style::default().fg(DIM)),
                        ]))
                    })
                    .collect();
                f.render_widget(List::new(items).block(block(&title)), chunks[1]);
            }
        }
    }

    let hint = match &app.screen {
        Screen::Main { .. } => "↑↓ select · Enter choose · q quit",
        Screen::Scan { .. } => "Esc back",
        Screen::UrlInput { busy, .. } => {
            if *busy {
                "working..."
            } else {
                "Enter deploy · Esc back"
            }
        }
        Screen::Deploy { done, .. } => {
            if done.is_some() {
                "Enter back"
            } else {
                "working..."
            }
        }
        Screen::Services { .. } => "↑↓ select · action keys shown above · Esc back",
    };
    f.render_widget(
        Paragraph::new(Line::from(Span::styled(
            format!(" {hint}"),
            Style::default().fg(DIM),
        ))),
        chunks[2],
    );
}

fn log_line(line: &str) -> Line<'static> {
    let style = if line.starts_with('!') || line.contains("failed") || line.contains("ERROR") {
        Style::default().fg(ERR_RED)
    } else if line.contains("complete") || line.contains("listening") {
        Style::default().fg(OK_GREEN)
    } else {
        Style::default().fg(Color::Reset)
    };
    Line::from(Span::styled(format!(" {line}"), style))
}

fn block(title: &str) -> Block<'_> {
    Block::default().borders(Borders::ALL).title(Span::styled(
        format!(" {title} "),
        Style::default().fg(ACCENT),
    ))
}
