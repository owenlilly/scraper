"""Visual design: a reading desk. Slate-green board, bone text, one highlighter accent.

The highlighter is the only decorative colour. It appears on pages the model used for
its last answer, on the primary button and on focus rings, so it always means something.
Everything is offline: system font stacks only.
"""

from nicegui import ui

SERIF = '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif'
SANS = 'ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'

CSS = """
:root {
  --pc-board: #1B2321;
  --pc-paper: #222C29;
  --pc-raised: #2A3633;
  --pc-rule: #3A4945;
  --pc-text: #E6E2D6;
  --pc-muted: #A3AAA1;
  --pc-blue: #7FA6C9;
  --pc-mark: #E6D04A;
  --pc-bad: #D9564E;
  --pc-warn: #E0A84A;
  --pc-ok: #7FBF8E;
  --pc-serif: %(serif)s;
  --pc-sans: %(sans)s;
}
body, body.body--dark, .q-page, .q-layout { background: var(--pc-board) !important; color: var(--pc-text); font-family: var(--pc-sans); }

/* top bar */
.pc-header { background: var(--pc-board) !important; border-bottom: 1px solid var(--pc-rule); flex-wrap: wrap; row-gap: 0.5rem; }
.pc-wordmark { font-family: var(--pc-serif); font-size: 1.2rem; font-weight: 600; letter-spacing: 0.01em; margin-right: 0.5rem; }
.pc-header .q-field--outlined .q-field__control:before { border-color: var(--pc-rule); }
.q-btn.bg-primary { color: #1B2321 !important; font-weight: 600; }
.q-btn { text-transform: none; letter-spacing: 0; }

/* panes */
.q-splitter__separator { background: var(--pc-rule) !important; }
.pc-pane-title { font-size: 0.85rem; font-weight: 600; color: var(--pc-muted); padding: 0.9rem 1rem 0.35rem; }
.pc-hint { font-size: 0.8rem; color: var(--pc-muted); padding: 0.25rem 1rem; }
.pc-source { padding: 0.5rem 1rem; border-bottom: 1px solid var(--pc-rule); }
.pc-source:hover { background: var(--pc-raised); }
.pc-source-title { font-weight: 600; width: fit-content; max-width: 100%%; background-image: linear-gradient(transparent 62%%, rgba(230, 208, 74, 0.55) 62%%); background-repeat: no-repeat; background-size: 0%% 100%%; }
.pc-source--used .pc-source-title { background-size: 100%% 100%%; animation: pc-swash 0.55s ease-out; }
.pc-source-meta { font-size: 0.78rem; color: var(--pc-muted); overflow-wrap: anywhere; }
.pc-failed { font-size: 0.8rem; color: var(--pc-bad); padding: 0.35rem 1rem; }
@keyframes pc-swash { from { background-size: 0%% 100%%; } to { background-size: 100%% 100%%; } }

/* the page as paper */
.pc-preview { background: var(--pc-paper); margin: 0.5rem 1rem 1rem; padding: 1rem 1.4rem; border-radius: 4px; font-family: var(--pc-serif); line-height: 1.65; max-width: 70ch; }
.pc-preview h1, .pc-preview h2, .pc-preview h3 { font-family: var(--pc-sans); line-height: 1.25; margin: 1.1em 0 0.4em; }
.pc-preview a { color: var(--pc-blue); }
.pc-preview h1 { font-size: 1.35rem; font-weight: 600; }
.pc-preview h2 { font-size: 1.15rem; font-weight: 600; }
.pc-preview h3 { font-size: 1rem; font-weight: 600; }
.pc-preview hr { border: 0; border-top: 1px solid var(--pc-rule); margin: 0.8rem 0; }

/* chat */
.pc-chat { padding: 0.5rem 1.25rem; max-width: 76ch; }
.pc-empty { color: var(--pc-muted); font-size: 0.95rem; padding: 2rem 0; max-width: 46ch; line-height: 1.5; }
.pc-chat .q-message-text-content { color: var(--pc-text) !important; }
.pc-chat .q-message-name { color: var(--pc-muted); font-size: 0.78rem; }
.pc-chat .q-message-sent .q-message-text { background: #2C4254 !important; color: var(--pc-text) !important; border-radius: 10px; }
.pc-chat .q-message-received .q-message-text { background: transparent !important; color: var(--pc-text) !important; padding: 0 0 0 14px; border-left: 2px solid var(--pc-rule); border-radius: 0; font-family: var(--pc-serif); font-size: 1.05rem; line-height: 1.6; }
.pc-chat .q-message-text:last-child:before, .pc-chat .q-message-text:before { display: none; }
.pc-waiting { color: var(--pc-muted); font-style: italic; }
.pc-reasoning { color: var(--pc-muted); font-size: 0.85rem; font-family: var(--pc-sans); }
.pc-reasoning .q-icon { font-size: 1.1rem; color: var(--pc-muted); }
.pc-reasoning .q-item { min-height: 32px; padding-left: 0; }
.pc-cite { font-family: var(--pc-sans); font-size: 0.78rem; color: var(--pc-muted); overflow-wrap: anywhere; }
.pc-interrupted { font-family: var(--pc-sans); font-size: 0.8rem; color: var(--pc-bad); }
.pc-compose { padding: 0.5rem 1.25rem 1rem calc(1.25rem + 1rem); max-width: 76ch; }

/* notices and status */
.pc-notice { border-left: 3px solid var(--pc-bad); background: rgba(217, 86, 78, 0.12); padding: 0.6rem 1rem; margin: 0.5rem 0; width: 100%%; }
.pc-notice-title { font-weight: 600; }
.pc-notice-hint { color: var(--pc-muted); font-size: 0.88rem; }
.pc-footer { background: var(--pc-board) !important; border-top: 1px solid var(--pc-rule); color: var(--pc-muted); }
.pc-dot { font-size: 0.7rem; }
.pc-hosted { border: 1px solid var(--pc-warn); color: var(--pc-warn); border-radius: 4px; padding: 0.1rem 0.5rem; font-size: 0.8rem; }
.pc-dot--ok { color: var(--pc-ok); }
.pc-dot--bad { color: var(--pc-bad); }

/* accessibility */
:focus-visible { outline: 2px solid var(--pc-mark); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
""" % {"serif": SERIF, "sans": SANS}


def apply_theme() -> None:
    ui.colors(
        primary="#E6D04A",
        secondary="#7FA6C9",
        accent="#E6D04A",
        dark="#1B2321",
        positive="#7FBF8E",
        negative="#D9564E",
        warning="#E0A84A",
    )
    ui.add_css(CSS)
    ui.add_css(AGENT_CSS)


AGENT_CSS = """
.pc-step { font-size: 0.8rem; color: var(--pc-muted); }
.pc-log { margin: 0.25rem 0 0.5rem; }
.pc-tool { border-left: 2px solid var(--pc-rule); font-size: 0.85rem; color: var(--pc-muted); }
.pc-tool .q-item { min-height: 30px; padding-left: 0.6rem; }
.pc-tool .q-icon { font-size: 1rem; }
.pc-tool-result { white-space: pre-wrap; word-break: break-word; font-size: 0.8rem; color: var(--pc-text); padding: 0.25rem 0.6rem 0.5rem; }
.pc-pause { padding: 0 1.25rem; max-width: 76ch; }
.pc-pause-card { border: 1px solid var(--pc-mark); background: rgba(230, 208, 74, 0.08); padding: 0.8rem 1rem; border-radius: 4px; width: 100%; }
.pc-pause-question { font-family: var(--pc-serif); font-size: 1.05rem; margin-bottom: 0.6rem; }
.pc-notice--info { border-left-color: var(--pc-blue); background: rgba(127, 166, 201, 0.12); }
.pc-queued { padding: 0 1.25rem; max-width: 76ch; }
.pc-chat .pc-queued-msg .q-message-text { background: transparent !important; border: 1px dashed var(--pc-blue); }
.pc-queued-note { font-family: var(--pc-sans); font-size: 0.75rem; color: var(--pc-muted); }
.pc-results { padding: 0.25rem 1.25rem; max-width: 76ch; }
.pc-table { background: var(--pc-paper); max-height: 14rem; }
.pc-left, .pc-right { height: calc(100vh - 8.6rem); flex-wrap: nowrap; }
.pc-left { overflow-y: auto; }
.pc-chat-scroll { flex: 1 1 0; height: auto !important; min-height: 8rem; }
.pc-notices { padding: 0 1.25rem; max-width: 76ch; }
.pc-results .pc-table { max-height: 9rem; }
"""
