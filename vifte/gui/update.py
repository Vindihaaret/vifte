"""Oppdateringer fra appen: manuell sjekk, månedlig automatisk sjekk og installasjon."""
import re
import shutil
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import __version__, updater  # noqa: E402
from ..i18n import _, get_setting, set_setting  # noqa: E402

FIRST_CHECK_DELAY = 60          # s etter oppstart (nettet er kanskje ikke oppe ved innlogging)
RECHECK_EVERY = 6 * 3600        # hvor ofte vi ser om det har gått en måned


def auto_enabled():
    return bool(get_setting("auto_update_check", True))


def last_checked():
    return get_setting("last_update_check")


def _plain_notes(md, limit=900):
    """Gjør utgivelsesnotater (Markdown) lesbare som ren tekst."""
    out = []
    for line in md.splitlines():
        if line.strip().startswith("```"):
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"\*\*?([^*]+)\*\*?", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        out.append(line.replace("- ", "• ", 1) if line.lstrip().startswith("- ") else line)
    text = "\n".join(out).strip()
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


class UpdateManager:
    def __init__(self, app):
        self.app = app
        self.latest = None
        self._busy = False
        action = Gio.SimpleAction.new("show-update", None)
        action.connect("activate", lambda *_a: self.latest and self.show_dialog(self.latest))
        app.add_action(action)

    # ------------------------------------------------------------ sjekk
    def start_auto(self):
        GLib.timeout_add_seconds(FIRST_CHECK_DELAY, self._auto_tick)

    def _auto_tick(self):
        if updater.due(last_checked(), enabled=auto_enabled()):
            self.check(manual=False)
        GLib.timeout_add_seconds(RECHECK_EVERY, self._auto_tick)
        return False

    def check(self, manual=True):
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                info, err = updater.fetch_latest(), None
            except Exception as ex:
                info, err = None, ex
            GLib.idle_add(self._done, info, err, manual)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, info, err, manual):
        self._busy = False
        if err is not None:
            print("oppdateringssjekk feilet:", err, file=sys.stderr)
            if manual:
                self._message(_("Kunne ikke se etter oppdateringer"), _("Feil: {}").format(err))
            return False
        set_setting("last_update_check", time.time())
        newer = updater.is_newer(info["tag"])
        if not newer:
            if manual:
                self._message(_("Vifte er oppdatert"),
                              _("Du har den nyeste versjonen ({}).").format(__version__))
            return False
        self.latest = info
        if not manual and get_setting("skipped_version") == info["tag"]:
            return False
        win = self.app.window
        if manual or (win is not None and win.get_visible()):
            self.show_dialog(info)
        else:
            n = Gio.Notification.new(_("Vifte {} er tilgjengelig").format(info["version"]))
            n.set_body(_("Klikk for å se hva som er nytt og oppdatere."))
            n.set_default_action("app.show-update")
            self.app.send_notification("vifte-update", n)
        return False

    # ------------------------------------------------------------ dialoger
    def _window(self):
        self.app.show_window()
        return self.app.window

    def _message(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", _("OK"))
        dlg.present(self._window())

    def show_dialog(self, info):
        win = self._window()
        installed = updater.is_installed()
        body = _("Du har versjon {}.").format(__version__)
        if not installed:
            body += "\n\n" + _("Vifte kjører fra en kildekodemappe. Oppdater med «git pull» og kjør install.sh.")
        dlg = Adw.AlertDialog(heading=_("Vifte {} er tilgjengelig").format(info["version"]), body=body)
        if info["notes"]:
            label = Gtk.Label(label=_plain_notes(info["notes"]), wrap=True, xalign=0, valign=Gtk.Align.START,
                              margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
            sw = Gtk.ScrolledWindow(child=label, min_content_height=120, max_content_height=260,
                                    propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            sw.add_css_class("card")
            dlg.set_extra_child(sw)
        dlg.add_response("skip", _("Hopp over denne versjonen"))
        dlg.add_response("later", _("Senere"))
        if installed:
            dlg.add_response("update", _("Oppdater nå"))
            dlg.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)
            dlg.set_default_response("update")
        else:
            dlg.add_response("open", _("Åpne GitHub"))
            dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_close_response("later")

        def resp(_d, r):
            if r == "skip":
                set_setting("skipped_version", info["tag"])
            elif r == "open":
                Gtk.UriLauncher.new(info["url"]).launch(win, None, None)
            elif r == "update":
                self.install(info)
        dlg.connect("response", resp)
        dlg.present(win)

    # ------------------------------------------------------------ installasjon
    def install(self, info):
        win = self._window()
        progress = Adw.AlertDialog(heading=_("Oppdaterer Vifte …"), body=_("Laster ned versjon {} …").format(
            info["version"]))
        spinner = Gtk.Spinner(spinning=True, height_request=32)
        progress.set_extra_child(spinner)
        progress.add_response("wait", _("Vent …"))
        progress.set_response_enabled("wait", False)
        progress.set_can_close(False)
        progress.present(win)
        state = {"src": None}

        def finish(heading=None, body=None):
            progress.set_can_close(True)
            progress.force_close()
            if state["src"]:
                shutil.rmtree(state["src"].rsplit("/src/", 1)[0], ignore_errors=True)
            if heading:
                self._message(heading, body)

        def downloaded(src, err):
            if err is not None:
                finish(_("Oppdateringen feilet"), _("Feil: {}").format(err))
                return False
            state["src"] = src
            progress.set_body(_("Installerer … Godkjenn med passordet ditt."))
            argv = ["pkexec", "/bin/bash", f"{src}/install.sh"]
            try:
                proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            except GLib.Error as ex:
                finish(_("Oppdateringen feilet"), _("Feil: {}").format(ex.message))
                return False
            proc.communicate_utf8_async(None, None, installed, proc)
            return False

        def installed(proc, res, _p):
            try:
                _ok, out, _err = proc.communicate_utf8_finish(res)
            except GLib.Error as ex:
                out = ex.message
            if proc.get_successful():
                finish()
                self._restart_dialog(info)
            elif proc.get_exit_status() in (126, 127):     # passorddialogen ble avbrutt
                finish()
                self.app.window.toast(_("Oppdateringen ble avbrutt"))
            else:
                finish(_("Oppdateringen feilet"), (out or "").strip()[-600:])

        def work():
            try:
                src, err = updater.download(info["tag"]), None
            except Exception as ex:
                src, err = None, ex
            GLib.idle_add(downloaded, src, err)
        threading.Thread(target=work, daemon=True).start()

    def _restart_dialog(self, info):
        dlg = Adw.AlertDialog(heading=_("Vifte er oppdatert til {}").format(info["version"]),
                              body=_("Programmet startes på nytt nå."))
        dlg.add_response("ok", _("OK"))
        dlg.connect("response", lambda *_a: self.restart())
        dlg.present(self._window())

    def restart(self):
        # Ny prosess etter at denne har avsluttet (appen er én instans om gangen).
        exe = shutil.which("vifte") or "/usr/local/bin/vifte"
        Gio.Subprocess.new(["/bin/sh", "-c", f"sleep 2; exec '{exe}' gui"], Gio.SubprocessFlags.NONE)
        self.app.quit_all()
