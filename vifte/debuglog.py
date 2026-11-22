"""Feilsøkingslogg.

Med «Feilsøkingslogg» slått på (Innstillinger → Generelt, settings["debug"]) skriver Vifte-prosessene som kjører som
brukeren – vinduet, statusfeltet, MCP-serveren for Claude og kommandolinjen – det de gjør til
~/.local/state/vifte/logs/vifte-<dato>.log: kallene til tjenesten vifted med svar eller feil, oppdateringer med
utdata fra install.sh, og uventede feil med full sporing. Tjenesten selv (root) logger til journalen som før.

Mappen er bare lesbar for brukeren, filene slettes etter KEEP_DAYS dager, og en dagsfil stopper ved MAX_BYTES.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime

KEEP_DAYS = 14
MAX_BYTES = 20 * 10**6
# Kall som går hvert sekund: logges bare når de feiler, ellers drukner resten
QUIET = {"state", "history", "mcp_tools"}

_logger = logging.getLogger("vifte")
_ready = False


def log_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "vifte", "logs")


def _open_dir():
    d = log_dir()
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)
    return d


class _Handler(logging.Handler):
    """Skriver til dagsfilen når feilsøkingsmodus er på. Innstillingen sjekkes høyst hvert 5. sekund, så den kan slås
    av og på mens prosesser kjører (statusfeltet går i dagevis)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self._checked = -1e9
        self._on = False
        self._mutex = threading.Lock()

    def enabled(self):
        now = time.monotonic()
        if now - self._checked > 5:
            self._checked = now
            try:
                from .i18n import get_setting
                self._on = bool(get_setting("debug", False))
            except Exception:  # loggen skal aldri stoppe Vifte
                self._on = False
        return self._on

    def emit(self, record):
        if not self.enabled():
            return
        try:
            line = self.format(record)
            with self._mutex:
                f = os.path.join(_open_dir(), f"vifte-{datetime.now():%Y-%m-%d}.log")
                if os.path.exists(f) and os.path.getsize(f) > MAX_BYTES:
                    return
                fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception:
            pass


def _handler():
    return next((h for h in _logger.handlers if isinstance(h, _Handler)), None)


def refresh():
    """Les innstillingen på nytt med en gang (når den nettopp er endret i denne prosessen)."""
    h = _handler()
    if h:
        h._checked = -1e9


def enabled():
    h = _handler()
    return bool(h and h.enabled())


def setup(role):
    """Kalles én gang når en prosess starter. role: «window», «tray», «mcp» eller «cli»."""
    global _ready
    if _ready or os.geteuid() == 0:     # root (tjenesten, sudo) har ingen brukerinnstillinger å følge
        return
    _ready = True
    h = _Handler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(process)d " + role + "] %(levelname)s %(message)s"))
    _logger.addHandler(h)
    _logger.setLevel(logging.DEBUG)
    _logger.propagate = False

    old_hook = sys.excepthook

    def hook(kind, value, tb):
        _logger.error("Unexpected error", exc_info=(kind, value, tb))
        old_hook(kind, value, tb)
    sys.excepthook = hook
    old_thread_hook = threading.excepthook

    def thread_hook(args):
        _logger.error("Unexpected error in thread %s", args.thread.name if args.thread else "?",
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        old_thread_hook(args)
    threading.excepthook = thread_hook
    if h.enabled():
        started(role)
        cleanup()


def started(role):
    from . import __version__
    _logger.info("Vifte %s (%s), Python %s, argv %s", __version__, role, sys.version.split()[0], sys.argv[1:])


def cleanup():
    """Sletter logger som er eldre enn KEEP_DAYS dager."""
    limit = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(log_dir()):
            f = os.path.join(log_dir(), name)
            if name.endswith(".log") and os.path.getmtime(f) < limit:
                os.unlink(f)
    except OSError:
        pass


def latest():
    """Den nyeste loggfilen, eller None."""
    try:
        logs = [os.path.join(log_dir(), n) for n in os.listdir(log_dir()) if n.endswith(".log")]
    except OSError:
        return None
    return max(logs, key=os.path.getmtime) if logs else None


def debug(msg, *args):
    _logger.debug(msg, *args)


def info(msg, *args):
    _logger.info(msg, *args)


def error(msg, *args, exc=False):
    _logger.error(msg, *args, exc_info=exc)


_seen = set()


def error_once(key, msg, *args):
    """En feil som kan gjenta seg hvert sekund (oppdatering av en rad): logges med sporing første gang."""
    if key in _seen or not enabled():
        return
    _seen.add(key)
    _logger.error(msg, *args, exc_info=True)


def short(value, limit=300):
    """Argumenter i kortform: hele konfigurasjoner (apply_config) blir bare en lengde."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else f"<{len(text)} chars>"


def _tail(text, lines=40):
    parts = [p if len(p) <= 500 else p[:500] + " …" for p in (text or "").rstrip().splitlines()]
    if len(parts) > lines:
        parts = [f"… ({len(parts) - lines} lines left out)"] + parts[-lines:]
    return "\n    ".join(parts)


def call(cmd, args, ms, result=None, err=None):
    """Ett kall til tjenesten. Kall i QUIET logges bare når de feiler."""
    if not enabled() or (err is None and cmd in QUIET):
        return
    text = f"vifted {cmd} {' '.join(f'{k}={short(v)}' for k, v in args.items())}".rstrip() + f"  ({ms:.0f} ms)"
    if err is not None:
        _logger.error("%s → %s: %s", text, type(err).__name__, err)
    else:
        _logger.debug("%s → %s", text, short(result))


def command(cmd, code=None, out=""):
    """Én kjørt kommando med utfallet (utdata kortes ned til slutten)."""
    if not enabled():
        return
    text = "$ " + " ".join(str(c) for c in cmd)
    if code is not None:
        text += f"  → exit {code}"
    if out and out.strip():
        text += "\n  output:\n    " + _tail(out)
    _logger.debug(text)
