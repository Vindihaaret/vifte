"""Hovedvinduet."""
import os
import shutil
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..client import Client  # noqa: E402
from ..config import (CURVE_TITLES, CURVE_TYPES, SENSOR_TYPES, FanControlConfig, curve_type,  # noqa: E402
                      custom_sensor_type, new_curve, new_custom_sensor, parse_point)
from .. import i18n  # noqa: E402
from ..i18n import LANGUAGES, _, language_setting, set_language_setting  # noqa: E402
from .common import Model, combo_row, spin_row, switch_row  # noqa: E402
from .dialogs import ControlDialog, CurveDialog, CustomSensorDialog, RemapDialog, RenameDialog  # noqa: E402
from .graph import CurveGraph  # noqa: E402
from .tray import Tray  # noqa: E402

APP_ID = "no.vifte.Vifte"
UNINSTALLER = "/usr/local/bin/vifte-uninstall"
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
AUTOSTART_SYSTEM = "/etc/xdg/autostart/no.vifte.Vifte-tray.desktop"

CSS = """
.vifte-card { padding: 14px; min-width: 260px; }
.vifte-big { font-size: 26px; font-weight: 700; font-feature-settings: "tnum"; }
.vifte-sub { font-feature-settings: "tnum"; }
.vifte-missing { color: @error_color; }
.vifte-hidden { opacity: 0.5; }
banner.error > revealer > widget { background-color: @error_bg_color; color: @error_fg_color; }
"""


def parse_color(c):
    """FanControl lagrer farger som «#AARRGGBB»."""
    if not c or not isinstance(c, str) or not c.startswith("#"):
        return None
    h = c[1:]
    if len(h) == 8:
        h = h[2:]
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def _user_autostart():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart", os.path.basename(AUTOSTART_SYSTEM))


def autostart_enabled():
    try:
        with open(_user_autostart()) as f:
            return "Hidden=true" not in f.read()
    except OSError:
        return True


def set_autostart(on):
    """Systemfilen i /etc/xdg/autostart gjelder alle; en brukerfil med Hidden=true slår den av."""
    path = _user_autostart()
    if on:
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Vifte\nExec=vifte tray\nHidden=true\n")


def task_message(task):
    msg = task.get("msg")
    if msg:
        return _(msg[0]).format(*[_(a) for a in msg[1]])
    return task.get("message", "")


class Card(Gtk.Box):
    def __init__(self, title, subtitle=None, on_settings=None, hidden=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
        self.add_css_class("card")
        self.add_css_class("vifte-card")
        if hidden:
            self.add_css_class("vifte-hidden")
        head = Gtk.Box(spacing=6)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=3)
        self.title.add_css_class("heading")
        titles.append(self.title)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0, ellipsize=3)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            titles.append(sub)
        head.append(titles)
        if on_settings:
            b = Gtk.Button(icon_name="emblem-system-symbolic", valign=Gtk.Align.START,
                           tooltip_text=_("Innstillinger"))
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: on_settings())
            head.append(b)
        self.append(head)


class Section(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                           margin_bottom=24, margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=1400, child=self.box)
        self.set_child(clamp)
        self.flow = None

    def clear(self):
        while (c := self.box.get_first_child()):
            self.box.remove(c)

    def new_flow(self, title=None):
        if title:
            lab = Gtk.Label(label=title, xalign=0, margin_top=6)
            lab.add_css_class("title-4")
            self.box.append(lab)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                max_children_per_line=6, min_children_per_line=1,
                                column_spacing=12, row_spacing=12)
        self.box.append(self.flow)
        return self.flow


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Vifte", default_width=1200, default_height=800)
        self.model = Model(Client())
        self.live_dialogs = set()
        self.updaters = []
        self.connected = False
        self._structure_key = None

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        self.toasts.set_child(tv)
        self.set_content(self.toasts)

        header = Adw.HeaderBar()
        tv.add_top_bar(header)
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        # venstre: profilvelger
        self.profile_btn = Gtk.MenuButton(tooltip_text=_("Konfigurasjon (profil)"))
        self.profile_btn.set_always_show_arrow(True)
        header.pack_start(self.profile_btn)

        # høyre: meny og lagre
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append(_("Importer FanControl-konfigurasjon …"), "win.import")
        sec.append(_("Eksporter konfigurasjon …"), "win.export")
        sec.append(_("Ny tom konfigurasjon …"), "win.new-config")
        sec.append(_("Lagre som …"), "win.save-as")
        sec.append(_("Slett konfigurasjon …"), "win.delete-config")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append(_("Par vifter automatisk"), "win.pair")
        sec.append(_("Koble manglende sensorer …"), "win.remap")
        sec.append(_("Søk etter maskinvare på nytt"), "win.rescan")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append(_("Vis skjulte kort"), "win.show-hidden")
        sec.append(_("Fahrenheit"), "win.fahrenheit")
        sec.append(_("Pause styring (BIOS overtar)"), "win.pause")
        sec.append(_("Innstillinger …"), "win.settings")
        sec.append(_("Om Vifte"), "win.about")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append(_("Avinstaller Vifte …"), "win.uninstall")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append(_("Avslutt"), "win.quit")
        menu.append_section(None, sec)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Meny"))
        header.pack_end(mb)
        self.save_btn = Gtk.Button(label=_("Lagre"), tooltip_text=_("Lagre konfigurasjonen (Ctrl+S)"))
        self.save_btn.connect("clicked", lambda *_: self.save())
        header.pack_end(self.save_btn)

        for name, cb in (("import", self.import_config), ("export", self.export_config),
                         ("new-config", self.new_config), ("save-as", self.save_as),
                         ("delete-config", self.delete_config), ("pair", self.pair),
                         ("remap", lambda: RemapDialog(self).present(self)), ("rescan", self.rescan),
                         ("settings", self.settings), ("about", self.about), ("uninstall", self.uninstall),
                         ("quit", self.request_quit)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        for name, cb in (("show-hidden", self.toggle_hidden), ("fahrenheit", self.toggle_fahrenheit),
                         ("pause", self.toggle_pause)):
            a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(False))
            a.connect("activate", lambda act, _p, cb=cb: cb(act))
            self.add_action(a)
        sc = Gtk.ShortcutController()
        sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>s"),
                                     action=Gtk.CallbackAction.new(lambda *_: self.save() or True)))
        sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>q"),
                                     action=Gtk.CallbackAction.new(lambda *_: self.request_quit() or True)))
        self.add_controller(sc)

        # bannere
        self.banner = Adw.Banner()
        self.banner.connect("button-clicked", self.banner_clicked)
        tv.add_top_bar(self.banner)
        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("osd")
        tv.add_top_bar(self.progress)

        self.pages = {}
        for key, title, icon in (("controls", _("Kontroller"), "preferences-system-symbolic"),
                                 ("curves", _("Kurver"), "power-profile-performance-symbolic"),
                                 ("speeds", _("Hastigheter"), "weather-windy-symbolic"),
                                 ("sensors", _("Sensorer"), "temperature-symbolic")):
            sec = Section()
            self.pages[key] = sec
            self.stack.add_titled_with_icon(sec, key, title, icon)
        tv.set_content(self.stack)

        bar = Adw.ViewSwitcherBar(stack=self.stack)
        tv.add_bottom_bar(bar)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 700sp"))
        bp.add_setter(bar, "reveal", True)
        bp.add_setter(switcher, "visible", False)
        self.add_breakpoint(bp)

        self.connect("close-request", self.on_close)
        self.poll()
        self._timer = GLib.timeout_add(1000, self.poll)

    def on_close(self, *_a):
        app = self.get_application()
        if app.tray and app.tray.available:
            self.set_visible(False)  # lukk til statusfeltet, som i FanControl
            return True
        return False

    # ================================================================ tjeneste
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def call(self, cmd, **kw):
        try:
            return self.model.client.call(cmd, **kw)
        except Exception as ex:
            self.toast(_("Feil: {}").format(_(str(ex))))
            return None

    def poll(self):
        m = self.model
        try:
            m.state = m.client.call("state")
            for item in m.state.get("sensors", []) + m.state.get("controls", []):
                if item.get("hardware", "").startswith("Hovedkort"):
                    item["hardware"] = _("Hovedkort") + item["hardware"][len("Hovedkort"):]
            if not self.connected or m.state["rev"] != m.rev:
                m.fetch_config()
                self.connected = True
                self.rebuild()
        except Exception as ex:
            if self.connected or self._structure_key is None:
                self.connected = False
                self._structure_key = "offline"
                self.show_offline(ex)
            return True
        self.update_values()
        return True

    def show_offline(self, ex):
        self.banner.set_title(_("Får ikke kontakt med tjenesten vifted ({}). "
                                "Kjør: sudo systemctl enable --now vifted").format(ex))
        self.banner.set_button_label("")
        self.banner.set_revealed(True)
        for p in self.pages.values():
            p.clear()

    def config_changed(self, rebuild=True):
        try:
            self.model.push()
        except Exception as ex:
            self.toast(_("Kunne ikke sende endring: {}").format(_(str(ex))))
        if rebuild:
            self.rebuild()

    def save(self):
        try:
            self.model.client.call("save")
            self.model.state["dirty"] = False
            self.toast(_("Lagret {}").format(self.model.name))
            self.update_header()
        except Exception as ex:
            self.toast(_("Feil ved lagring: {}").format(_(str(ex))))

    # ================================================================ oppbygging
    def apply_theme(self):
        """Samme felter som FanControl: SyncThemeWithWindows = følg systemet, ellers SelectedTheme."""
        w = self.model.cfg.window
        sm = Adw.StyleManager.get_default()
        if w.get("SyncThemeWithWindows", True) or not w.get("SelectedTheme"):
            sm.set_color_scheme(Adw.ColorScheme.DEFAULT)
        elif str(w.get("SelectedTheme")).lower() == "light":
            sm.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def theme_choice(self):
        w = self.model.cfg.window
        if w.get("SyncThemeWithWindows", True) or not w.get("SelectedTheme"):
            return "system"
        return "light" if str(w.get("SelectedTheme")).lower() == "light" else "dark"

    def set_theme(self, choice):
        w = self.model.cfg.window
        w["SyncThemeWithWindows"] = choice == "system"
        if choice != "system":
            w["SelectedTheme"] = "Light" if choice == "light" else "Dark"
        self.apply_theme()
        self.config_changed(False)

    def rebuild(self):
        self.apply_theme()
        self.updaters = []
        self.build_profiles()
        self.build_controls()
        self.build_curves()
        self.build_speeds()
        self.build_sensors()
        for name, val in (("show-hidden", self.model.show_hidden), ("fahrenheit", self.model.fahrenheit),
                          ("pause", bool(self.model.state.get("paused")))):
            self.lookup_action(name).set_state(GLib.Variant.new_boolean(val))
        self.update_values()

    def build_profiles(self):
        m = self.model
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        lab = Gtk.Label(label=_("Konfigurasjoner"), xalign=0, margin_bottom=4)
        lab.add_css_class("heading")
        box.append(lab)
        for name in m.state.get("configs", []):
            b = Gtk.Button()
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Image(icon_name="object-select-symbolic", opacity=1 if name == m.name else 0))
            row.append(Gtk.Label(label=name[:-5] if name.endswith(".json") else name, xalign=0))
            b.set_child(row)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, n=name: (pop.popdown(), self.switch_config(n)))
            box.append(b)
        pop.set_child(box)
        self.profile_btn.set_popover(pop)
        self.update_header()

    def update_header(self):
        m = self.model
        name = m.name[:-5] if m.name.endswith(".json") else m.name
        dirty = m.state.get("dirty")
        self.profile_btn.set_label(name + (" •" if dirty else ""))
        if dirty:
            self.save_btn.add_css_class("suggested-action")
        else:
            self.save_btn.remove_css_class("suggested-action")

    def visible(self, entry):
        return self.model.show_hidden or not entry.get("IsHidden")

    # ---- kontroller
    def build_controls(self):
        m, cfg = self.model, self.model.cfg
        sec = self.pages["controls"]
        sec.clear()
        groups = {}
        for e in cfg.controls:
            if not self.visible(e):
                continue
            ident = e.get("Identifier")
            local = next((c for c in m.state.get("controls", []) if c["id"] == m.local_id(ident)), None)
            groups.setdefault(local["hardware"] if local else _("Mangler på denne maskinen"), []).append((e, local))
        if not groups:
            sec.box.append(Adw.StatusPage(icon_name="weather-windy-symbolic", title=_("Ingen styrbare vifter funnet"),
                                          description=_("Last inn driveren for hovedkortets Super I/O-brikke "
                                                      "(f.eks. «sudo modprobe nct6775») og søk på nytt.")))
        for hwname, items in sorted(groups.items(), key=lambda g: g[0] == _("Mangler på denne maskinen")):
            flow = sec.new_flow(hwname)
            for e, local in items:
                flow.append(self.control_card(e, local))

    def control_card(self, e, local):
        m, cfg = self.model, self.model.cfg
        ident = e.get("Identifier")
        card = Card(e.get("NickName") or e.get("Name") or ident, None if local else ident,
                    lambda: ControlDialog(self, ident).present(self), e.get("IsHidden"))
        row = Gtk.Box(spacing=8)
        sw = Gtk.Switch(active=bool(e.get("Enable")), valign=Gtk.Align.CENTER,
                        tooltip_text=_("Aktiver styring"))
        names = [_("(ingen kurve)")] + [c.get("Name") for c in cfg.curves]
        dd = Gtk.DropDown(model=Gtk.StringList.new(names), hexpand=True)
        cur = (e.get("SelectedFanCurve") or {}).get("Name")
        dd.set_selected(names.index(cur) if cur in names else 0)

        def on_switch(s, _p):
            cfg.control(ident)["Enable"] = s.get_active()
            self.config_changed(False)

        def on_curve(d, _p):
            i = d.get_selected()
            cfg.control(ident)["SelectedFanCurve"] = cfg.make_curve_ref(names[i]) if i else None
            if i and not cfg.control(ident).get("Enable"):
                cfg.control(ident)["Enable"] = True
                sw.set_active(True)
            self.config_changed(False)
        sw.connect("notify::active", on_switch)
        dd.connect("notify::selected", on_curve)
        row.append(sw)
        row.append(dd)
        card.append(row)

        vals = Gtk.Box(spacing=12, margin_top=4)
        big = Gtk.Label(label="–", xalign=0)
        big.add_css_class("vifte-big")
        rpm = Gtk.Label(label="", xalign=0, valign=Gtk.Align.END, margin_bottom=4, hexpand=True)
        rpm.add_css_class("vifte-sub")
        rpm.add_css_class("dim-label")
        manual = Gtk.Label(label="", valign=Gtk.Align.END, margin_bottom=4)
        manual.add_css_class("caption")
        manual.add_css_class("accent")
        vals.append(big)
        vals.append(rpm)
        vals.append(manual)
        card.append(vals)
        level = Gtk.LevelBar(min_value=0, max_value=100)
        card.append(level)

        def update():
            st = (m.state.get("control_state") or {}).get(ident, {})
            ent = cfg.control(ident) or {}
            out = st.get("out")
            read = st.get("read")
            if out is not None:
                big.set_label(f"{out:.0f} %")
                level.set_value(out)
            elif read is not None:
                big.set_label(f"{read:.0f} %")
                level.set_value(read)
            else:
                big.set_label("–")
                level.set_value(0)
            r = st.get("rpm")
            rpm.set_label(f"{r:.0f} RPM" if r is not None else (_("BIOS styrer") if out is None and local else ""))
            manual.set_label(_("MANUELL") if ent.get("ManualControl") and ent.get("Enable") else "")
        self.updaters.append(update)
        return card

    # ---- kurver
    def build_curves(self):
        cfg = self.model.cfg
        sec = self.pages["curves"]
        sec.clear()
        bar = Gtk.Box(spacing=8)
        bar.append(Gtk.Label(label=_("Kurver"), xalign=0, hexpand=True, css_classes=["title-4"]))
        add = Gtk.MenuButton(label=_("Ny kurve"), icon_name="list-add-symbolic", always_show_arrow=True)
        add.add_css_class("suggested-action")
        menu = Gio.Menu()
        for k in CURVE_TYPES:
            menu.append(_(CURVE_TITLES[k]), f"win.add-curve::{k}")
        add.set_menu_model(menu)
        if not self.lookup_action("add-curve"):
            a = Gio.SimpleAction.new("add-curve", GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p: self.add_curve(p.get_string()))
            self.add_action(a)
        bar.append(add)
        sec.box.append(bar)
        flow = sec.new_flow()
        for c in cfg.curves:
            if self.visible(c):
                flow.append(self.curve_card(c))
        if not cfg.curves:
            sec.box.append(Adw.StatusPage(icon_name="power-profile-performance-symbolic", title=_("Ingen kurver ennå"),
                                          description=_("Lag en kurve og velg den på en kontroll.")))

    def curve_card(self, c):
        m = self.model
        name = c.get("Name")
        kind = curve_type(c)
        src = (c.get("SelectedTempSource") or {}).get("Identifier")
        sub = _(CURVE_TITLES[kind])
        if src:
            sub += " · " + m.cfg.display_name(src, src)
        elif kind == "mix":
            sub += " · " + ", ".join(r.get("Name", "?") for r in c.get("SelectedFanCurves") or [])
        card = Card(name, sub, lambda: CurveDialog(self, name).present(self), c.get("IsHidden"))
        graph = None
        if kind in ("graph", "linear"):
            graph = CurveGraph(editable=False, height=110)
            if kind == "graph":
                graph.points = sorted(parse_point(p) for p in c.get("Points") or [])
                graph.x_range = (float(c.get("MinimumTemperature", 20)), float(c.get("MaximumTemperature", 120)))
                graph.y_range = (0, float(c.get("MaximumCommand", 100) or 100))
            else:
                graph.points = [(float(c.get("MinimumTemperature", 40)), float(c.get("MinimumFanSpeed", 20))),
                                (float(c.get("MaximumTemperature", 80)), float(c.get("MaximumFanSpeed", 100)))]
                graph.x_range = (20, 100)
            if graph.x_range[1] <= graph.x_range[0]:
                graph.x_range = (20, 120)
            graph.fahrenheit = m.fahrenheit
            card.append(graph)
        vals = Gtk.Box(spacing=12)
        big = Gtk.Label(label="–", xalign=0)
        big.add_css_class("vifte-big")
        inp = Gtk.Label(label="", xalign=0, valign=Gtk.Align.END, margin_bottom=4)
        inp.add_css_class("dim-label")
        vals.append(big)
        vals.append(inp)
        card.append(vals)
        unit = " RPM" if int(c.get("CommandMode", 0) or 0) == 1 else " %"

        def update():
            st = (m.state.get("curves") or {}).get(name) or {}
            v, t = st.get("value"), st.get("input")
            big.set_label("–" if v is None else f"{v:.0f}{unit}")
            inp.set_label(m.fmt_temp(t) if t is not None else (st.get("error") or ""))
            if src and t is None and m.temp_value(src) is None:
                inp.set_label(_("Sensor mangler"))
                inp.add_css_class("vifte-missing")
            if graph:
                graph.marker = (t, v)
                graph.queue_draw()
        self.updaters.append(update)
        return card

    def add_curve(self, kind):
        cfg = self.model.cfg
        base = _(CURVE_TITLES[kind])
        n = 1
        while cfg.curve(f"{base} {n}" if n > 1 else base):
            n += 1
        name = f"{base} {n}" if n > 1 else base
        src = cfg.temp_sensors[0].get("Identifier") if cfg.temp_sensors else None
        # foretrekk CPU-temperaturen som standardkilde
        for e in cfg.temp_sensors:
            if e.get("Identifier", "").startswith(("/amdcpu/", "/intelcpu/")):
                src = e["Identifier"]
                break
        cfg.curves.append(new_curve(kind, name, cfg, src))
        self.config_changed(True)
        CurveDialog(self, name).present(self)

    # ---- hastigheter
    def build_speeds(self):
        m, cfg = self.model, self.model.cfg
        sec = self.pages["speeds"]
        sec.clear()
        groups = {}
        for e in cfg.fan_sensors:
            if self.visible(e):
                s = m.sensor(e.get("Identifier"))
                groups.setdefault(s["hardware"] if s else _("Mangler på denne maskinen"), []).append(e)
        for hwname, items in sorted(groups.items(), key=lambda g: g[0] == _("Mangler på denne maskinen")):
            flow = sec.new_flow(hwname)
            for e in items:
                flow.append(self.value_card(e, "fan"))

    def value_card(self, e, kind):
        m = self.model
        ident = e.get("Identifier")
        card = Card(e.get("NickName") or e.get("Name") or ident, None, lambda: self.sensor_menu(ident),
                    e.get("IsHidden"))
        big = Gtk.Label(label="–", xalign=0)
        big.add_css_class("vifte-big")
        card.append(big)

        def update():
            v = m.temp_value(ident)
            if kind == "fan":
                big.set_label("–" if v is None else f"{v:.0f} RPM")
            else:
                big.set_label(m.fmt_temp(v))
        self.updaters.append(update)
        return card

    def sensor_menu(self, ident):
        entry = self.model.cfg.sensor_entry(ident)
        dlg = Adw.AlertDialog(heading=entry.get("NickName") or entry.get("Name") or ident, body=ident)
        dlg.add_response("cancel", _("Lukk"))
        dlg.add_response("hide", _("Vis") if entry.get("IsHidden") else _("Skjul"))
        dlg.add_response("rename", _("Gi nytt navn"))
        icons = self.model.cfg.window.setdefault("TrayIcons", [])
        in_tray = [t for t in icons if ((t or {}).get("SensorId") or {}).get("Identifier") == ident]
        if self.get_application().tray:
            dlg.add_response("tray", _("Fjern fra statusfeltet") if in_tray else _("Vis i statusfeltet"))

        def resp(_d, r):
            if r == "tray":
                if in_tray:
                    self.model.cfg.window["TrayIcons"] = [t for t in icons if t not in in_tray]
                else:
                    icons.append({"SensorId": self.model.cfg.make_ref(ident, self.model.cfg.display_name(ident)),
                                  "Color": None})
                self.config_changed(False)
                self.update_tray()
            elif r == "rename":
                RenameDialog.ask(self, _("Nytt navn"), entry.get("NickName") or entry.get("Name"),
                                 lambda v: (entry.__setitem__("NickName", v), self.config_changed(True)))
            elif r == "hide":
                entry["IsHidden"] = not entry.get("IsHidden")
                self.config_changed(True)
        dlg.connect("response", resp)
        dlg.present(self)

    # ---- sensorer
    def build_sensors(self):
        m, cfg = self.model, self.model.cfg
        sec = self.pages["sensors"]
        sec.clear()
        bar = Gtk.Box(spacing=8)
        bar.append(Gtk.Label(label=_("Egendefinerte sensorer"), xalign=0, hexpand=True, css_classes=["title-4"]))
        add = Gtk.MenuButton(label=_("Ny sensor"), icon_name="list-add-symbolic", always_show_arrow=True)
        menu = Gio.Menu()
        for k, t in SENSOR_TYPES.items():
            menu.append(_(t), f"win.add-sensor::{k}")
        add.set_menu_model(menu)
        if not self.lookup_action("add-sensor"):
            a = Gio.SimpleAction.new("add-sensor", GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, p: self.add_sensor(p.get_string()))
            self.add_action(a)
        bar.append(add)
        sec.box.append(bar)
        if cfg.custom_sensors:
            flow = sec.new_flow()
            for s in cfg.custom_sensors:
                ident = s.get("Identifier")
                card = Card(s.get("Name") or ident, _(SENSOR_TYPES[custom_sensor_type(s)]),
                            lambda i=ident: CustomSensorDialog(self, i).present(self))
                big = Gtk.Label(label="–", xalign=0)
                big.add_css_class("vifte-big")
                card.append(big)
                self.updaters.append(lambda b=big, i=ident: b.set_label(m.fmt_temp(m.temp_value(i))))
                flow.append(card)
        groups = {}
        for e in cfg.temp_sensors:
            if self.visible(e):
                s = m.sensor(e.get("Identifier"))
                groups.setdefault(s["hardware"] if s else _("Mangler på denne maskinen"), []).append(e)
        for hwname, items in sorted(groups.items(), key=lambda g: g[0] == _("Mangler på denne maskinen")):
            flow = sec.new_flow(hwname)
            for e in items:
                flow.append(self.value_card(e, "temperature"))

    def add_sensor(self, kind):
        cfg = self.model.cfg
        base = _(SENSOR_TYPES[kind])
        n = 1
        names = {s.get("Name") for s in cfg.custom_sensors}
        while (f"{base} {n}" if n > 1 else base) in names:
            n += 1
        s = new_custom_sensor(kind, f"{base} {n}" if n > 1 else base)
        cfg.custom_sensors.append(s)
        self.config_changed(True)
        CustomSensorDialog(self, s["Identifier"]).present(self)

    # ================================================================ løpende oppdatering
    def update_values(self):
        st = self.model.state
        for u in self.updaters:
            try:
                u()
            except Exception as ex:  # en defekt oppdatering skal ikke stoppe resten
                print("oppdatering:", ex, file=sys.stderr)
        for d in list(self.live_dialogs):
            d.update_live()
        self.update_header()
        self.update_tray()
        self.show_mcp_activity(st.get("mcp_log") or [])
        # bannere – nødmodus har høyest prioritet
        fs = st.get("failsafe") or {}
        if fs.get("active"):
            trig = fs.get("trigger") or [None, None]
            self.banner.set_title(_("NØDMODUS: {} er {} – alle styrte vifter går på 100 %").format(
                self.model.cfg.display_name(trig[0], trig[0]), self.model.fmt_temp(trig[1])))
            self.banner.set_button_label("")
            self.banner.add_css_class("error")
            self.banner.set_revealed(True)
            self.progress.set_visible(False)
            return
        self.banner.remove_css_class("error")
        task = st.get("task")
        if task and not task.get("done"):
            self.progress.set_visible(True)
            self.progress.set_fraction(task.get("progress", 0))
            self.banner.set_title((_("Kalibrerer: ") if task["kind"] == "calibrate" else _("Parer vifter: "))
                                  + task_message(task))
            self.banner.set_button_label(_("Avbryt"))
            self.banner_action = "cancel"
            self.banner.set_revealed(True)
            return
        self.progress.set_visible(False)
        if task and task.get("done"):
            if getattr(self, "_task_seen", None) != (task["kind"], task.get("message")):
                self._task_seen = (task["kind"], task.get("message"))
                self.toast((_("Kalibrering: ") if task["kind"] == "calibrate" else _("Paring: ")) + task_message(task))
        missing = st.get("missing") or []
        if missing:
            self.banner.set_title(_("{} sensor(er) i konfigurasjonen finnes ikke på denne maskinen").format(len(missing)))
            self.banner.set_button_label(_("Koble til …"))
            self.banner_action = "remap"
            self.banner.set_revealed(True)
        elif st.get("paused"):
            self.banner.set_title(_("Styring er satt på pause – BIOS/driver styrer viftene"))
            self.banner.set_button_label(_("Fortsett"))
            self.banner_action = "resume"
            self.banner.set_revealed(True)
        elif st.get("dry_run"):
            self.banner.set_title(_("Testmodus: ingenting skrives til maskinvaren"))
            self.banner.set_button_label("")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def update_tray(self):
        tray = self.get_application().tray
        if not tray or self.get_application().window is not self:
            return
        m = self.model
        st = m.state
        key = (tuple(st.get("configs", [])), m.name, bool(st.get("paused")), i18n.current(), self.connected)
        if key != getattr(self, "_tray_key", None):
            self._tray_key = key
            configs = [{"label": n[:-5] if n.endswith(".json") else n, "toggle": "radio",
                        "state": n == m.name, "action": (lambda n=n: self.switch_config(n))}
                       for n in st.get("configs", [])]
            tray.set_menu([
                {"label": _("Vis Vifte"), "action": self.get_application().show_window},
                {"separator": True},
                {"label": _("Konfigurasjoner"), "children": configs, "enabled": bool(configs)},
                {"label": _("Pause styring (BIOS overtar)"), "toggle": "checkmark",
                 "state": bool(st.get("paused")), "enabled": self.connected,
                 "action": lambda: self.call("settings", paused=not self.model.state.get("paused"))},
                {"separator": True},
                {"label": _("Avslutt"), "action": self.request_quit},
            ])
        wanted = {}
        default = parse_color(m.cfg.window.get("TrayIconColor")) or (1, 1, 1)
        for n, t in enumerate(m.cfg.window.get("TrayIcons") or []):
            ident = ((t or {}).get("SensorId") or {}).get("Identifier")
            if not ident:
                continue
            v = m.temp_value(ident)
            kind = "fan" if any(e.get("Identifier") == ident for e in m.cfg.fan_sensors) else "temp"
            if v is None:
                text = "–"
            elif kind == "fan":
                text = f"{v / 1000:.1f}k" if v >= 1000 else f"{v:.0f}"
            else:
                text = f"{v * 9 / 5 + 32:.0f}" if m.fahrenheit else f"{v:.0f}"
            name = m.cfg.display_name(ident, ident)
            tip = f"{name}: " + (m.fmt_temp(v) if kind == "temp" else ("–" if v is None else f"{v:.0f} RPM"))
            wanted[f"{n}:{ident}"] = (name, text, parse_color(t.get("Color")) or default, tip)
        tray.set_sensors(wanted)

    def show_mcp_activity(self, entries):
        """Viser et varsel for hver ny endring Claude har gjort."""
        newest = max((e["id"] for e in entries), default=0)
        last = getattr(self, "_mcp_seen", None)
        self._mcp_seen = newest
        if last is None:
            return  # ikke vis gamle hendelser når vinduet åpnes
        for e in entries:
            if e["id"] > last:
                msg = e.get("msg") or [e.get("text", ""), []]
                self.toast("Claude: " + _(msg[0]).format(*msg[1]))

    def banner_clicked(self, _b):
        act = getattr(self, "banner_action", None)
        if act == "cancel":
            self.call("cancel")
        elif act == "remap":
            RemapDialog(self).present(self)
        elif act == "resume":
            self.call("settings", paused=False)

    # ================================================================ handlinger
    def switch_config(self, name):
        if name == self.model.name:
            return

        def go():
            if self.call("load", name=name) is not None:
                self.poll()
        if self.model.state.get("dirty"):
            self.confirm_discard(go)
        else:
            go()

    def confirm_discard(self, then):
        dlg = Adw.AlertDialog(heading=_("Ulagrede endringer"),
                              body=_("Vil du lagre endringene i {} først?").format(self.model.name))
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("discard", _("Forkast"))
        dlg.add_response("save", _("Lagre"))
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r == "save":
                self.save()
                then()
            elif r == "discard":
                then()
        dlg.connect("response", resp)
        dlg.present(self)

    def _json_filter(self):
        f = Gtk.FileFilter(name=_("FanControl-konfigurasjon (*.json)"))
        f.add_pattern("*.json")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(f)
        return store

    def import_config(self):
        fd = Gtk.FileDialog(title=_("Importer FanControl-konfigurasjon"), filters=self._json_filter())

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path()
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    text = fh.read()
            except OSError as ex:
                self.toast(_(str(ex)))
                return
            r = self.call("import", name=os.path.basename(path), text=text)
            if r:
                self.toast(_("Importert som {}").format(r["name"]))
                self.poll()
        fd.open(self, None, done)

    def export_config(self):
        fd = Gtk.FileDialog(title=_("Eksporter konfigurasjon"), initial_name=self.model.name,
                            filters=self._json_filter())

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            r = self.call("export")
            if r:
                with open(f.get_path(), "w", encoding="utf-8") as fh:
                    fh.write(r["text"])
                self.toast(_("Eksportert – filen kan brukes i FanControl på Windows"))
        fd.save(self, None, done)

    def new_config(self):
        def make(name):
            r = self.call("import", name=name, raw=FanControlConfig().raw)
            if r:
                self.poll()
        RenameDialog.ask(self, _("Navn på ny konfigurasjon"), _("Ny"), make)

    def save_as(self):
        def do(name):
            if self.call("save", name=name) is not None:
                self.poll()
                self.build_profiles()
        RenameDialog.ask(self, _("Lagre som"), self.model.name[:-5], do)

    def delete_config(self):
        others = [n for n in self.model.state.get("configs", []) if n != self.model.name]
        if not others:
            self.toast(_("Det finnes ingen andre konfigurasjoner å slette"))
            return
        dlg = Adw.AlertDialog(heading=_("Slett konfigurasjon"), body=_("Den aktive konfigurasjonen kan ikke slettes."))
        dd = Gtk.DropDown(model=Gtk.StringList.new(others))
        dlg.set_extra_child(dd)
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("delete", _("Slett"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.call("delete", name=others[dd.get_selected()])
                    is not None and self.poll())
        dlg.present(self)

    def pair(self):
        dlg = Adw.AlertDialog(heading=_("Par vifter automatisk"),
                              body=_("Hver vifteutgang kjøres kort på 100 % for å finne hvilken hastighetssensor "
                                   "som hører til. Det tar omtrent 15 sekunder per kontroll."))
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("go", _("Start"))
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "go" and self.call("pair"))
        dlg.present(self)

    def rescan(self):
        if self.call("rescan") is not None:
            self.toast(_("Maskinvare søkt på nytt"))

    def toggle_hidden(self, act):
        v = not act.get_state().get_boolean()
        self.model.cfg.window["ShowHiddenCards"] = v
        self.config_changed(True)

    def toggle_fahrenheit(self, act):
        v = not act.get_state().get_boolean()
        self.model.cfg.window["Fahrenheit"] = v
        self.config_changed(True)

    def toggle_pause(self, act):
        v = not act.get_state().get_boolean()
        act.set_state(GLib.Variant.new_boolean(v))
        self.call("settings", paused=v)

    def settings(self):
        dlg = Adw.PreferencesDialog(title=_("Innstillinger"))
        page = Adw.PreferencesPage()
        dlg.add(page)
        g = Adw.PreferencesGroup(title="Språk / Language")
        page.add(g)
        langs = [(k, _(label)) for k, label in LANGUAGES]
        g.add(combo_row("Språk / Language", langs, language_setting(),
                        lambda v: self.change_language(v, dlg)))
        if os.path.exists(AUTOSTART_SYSTEM):
            g = Adw.PreferencesGroup(title=_("Statusfelt"))
            page.add(g)
            g.add(switch_row(_("Start i statusfeltet ved innlogging"), autostart_enabled(), set_autostart,
                             _("Viser ikonet i statusfeltet uten å åpne vinduet")))
        self.build_mcp_settings(page)
        g = Adw.PreferencesGroup(title=_("Tjeneste"))
        page.add(g)
        g.add(spin_row(_("Oppdateringsintervall (s)"), self.model.state.get("interval", 1.0), 0.25, 10, 0.25,
                       lambda v: self.call("settings", interval=v), digits=2))
        g = Adw.PreferencesGroup(title=_("Visning"))
        page.add(g)
        w = self.model.cfg.window
        g.add(combo_row(_("Tema"), [("system", _("Følg systemet")), ("light", _("Lyst")), ("dark", _("Mørkt"))],
                        self.theme_choice(), self.set_theme))
        g.add(switch_row(_("Skjul hastighetskort"), w.get("HideFanSpeedCards"),
                         lambda v: (w.__setitem__("HideFanSpeedCards", v), self.apply_view())))
        self.apply_view()
        dlg.present(self)

    def build_mcp_settings(self, page):
        safety = self.model.state.get("safety") or {}
        g = Adw.PreferencesGroup(
            title=_("Claude (MCP)"),
            description=_("La Claude lese sensorer og justere kurver og vifter for deg. Sikkerhetsgrensene "
                          "under håndheves av Vifte og kan ikke overstyres av Claude."))
        page.add(g)
        enable = switch_row(_("Tillat styring fra Claude"), safety.get("mcp_enabled"), self.set_mcp_enabled,
                            _("Nødmodus er alltid på mens dette er slått på"))
        g.add(enable)
        g.add(spin_row(_("Laveste viftehastighet (%)"), safety.get("floor", 25), 10, 60, 1,
                       lambda v: self.call("settings", mcp_floor=v),
                       subtitle=_("Claude kan aldri sette en vifte lavere enn dette, og aldri stoppe den")))
        g.add(spin_row(_("Nødtemperatur (°C)"), safety.get("failsafe_temp", 95), 70, 105, 1,
                       lambda v: self.call("settings", failsafe_temp=v),
                       subtitle=_("Når CPU eller GPU når denne temperaturen, går alle styrte vifter på 100 %")))
        g.add(switch_row(_("Nødmodus også uten Claude"), safety.get("failsafe_enabled"),
                         lambda v: self.call("settings", failsafe_enabled=v),
                         _("Bruk nødtemperaturen selv om Claude-tilgang er av")))
        row = Adw.ActionRow(title=_("Koble til Claude Code"), subtitle="claude mcp add --scope user vifte -- vifte mcp")
        row.set_subtitle_selectable(True)
        if shutil.which("claude"):
            btn = Gtk.Button(label=_("Legg til"), valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_a: self.register_claude_code())
            row.add_suffix(btn)
        g.add(row)

    def set_mcp_enabled(self, on):
        if self.call("settings", mcp_enabled=on) is None:
            return
        if not on:
            self.toast(_("Claude-tilgang er slått av"))
            return
        # Ingen omstart trengs: tjenesten slipper Claude inn med en gang.
        dlg = Adw.AlertDialog(
            heading=_("Claude-tilgang er slått på"),
            body=_("Claude kan nå lese sensorer og endre kurver og vifter innenfor sikkerhetsgrensene. "
                   "Ingen omstart er nødvendig.\n\nEndringer tas i bruk med en gang, men lagres først når du "
                   "ber om det. Du ser et varsel her for hver endring Claude gjør.\n\n"
                   "Koble Vifte til Claude Code med:\nclaude mcp add --scope user vifte -- vifte mcp"))
        dlg.add_response("ok", _("OK"))
        if shutil.which("claude"):
            dlg.add_response("add", _("Legg til i Claude Code"))
            dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "add" and self.register_claude_code())
        dlg.present(self)

    def register_claude_code(self):
        exe = shutil.which("vifte") or "vifte"
        argv = ["claude", "mcp", "add", "--scope", "user", "vifte", "--", exe, "mcp"]
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as ex:
            self.toast(_("Feil: {}").format(ex.message))
            return

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error as ex:
                out = ex.message
            if p.get_successful() or "already exists" in (out or ""):
                self.toast(_("Vifte er lagt til i Claude Code. Start en ny Claude Code-økt for å bruke den."))
            else:
                self.toast(_("Feil: {}").format((out or "").strip()[-200:]))
        proc.communicate_utf8_async(None, None, done)

    def request_quit(self):
        """Avslutter vinduet og ikonet i statusfeltet. Tjenesten fortsetter å styre viftene."""
        app = self.get_application()
        if self.model.state.get("dirty"):
            self.present()
            self.confirm_discard(app.quit_all)
        else:
            app.quit_all()

    def change_language(self, lang, dlg):
        if lang == language_setting():
            return
        set_language_setting(lang)
        # Bygg vinduet på nytt med det nye språket.
        app = self.get_application()
        dlg.close()
        GLib.source_remove(self._timer)
        app.window = MainWindow(app)
        app.window.present()
        self.destroy()

    def uninstall(self):
        if not os.path.exists(UNINSTALLER):
            self.toast(_("Vifte ser ikke ut til å være installert (fant ikke {}).").format(UNINSTALLER))
            return
        dlg = Adw.AlertDialog(heading=_("Avinstaller Vifte"),
                              body=_("Tjenesten stoppes og viftene gis tilbake til BIOS. Programmet, "
                                     "menyoppføringen og driveroppsettet fjernes."))
        purge = Gtk.CheckButton(label=_("Slett også alle konfigurasjoner"))
        dlg.set_extra_child(purge)
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("uninstall", _("Avinstaller"))
        dlg.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda _d, r: r == "uninstall" and self.run_uninstall(purge.get_active()))
        dlg.present(self)

    def run_uninstall(self, purge):
        argv = ["pkexec", UNINSTALLER, "--yes"] + (["--purge"] if purge else [])
        try:
            proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        except GLib.Error as ex:
            self.toast(_("Avinstallering feilet: {}").format(ex.message))
            return

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_utf8_finish(res)
            except GLib.Error as ex:
                out = ex.message
            if p.get_successful():
                msg = Adw.AlertDialog(heading=_("Vifte er avinstallert."))
                msg.add_response("ok", _("OK"))
                msg.connect("response", lambda *_a: self.get_application().quit())
                msg.present(self)
            elif p.get_exit_status() not in (126, 127):  # 126/127: avbrutt i passorddialogen
                self.toast(_("Avinstallering feilet: {}").format((out or "").strip()[-200:]))
        proc.communicate_utf8_async(None, None, done)

    def apply_view(self):
        hide = bool(self.model.cfg.window.get("HideFanSpeedCards"))
        self.stack.get_page(self.pages["speeds"]).set_visible(not hide)
        self.config_changed(False)

    def about(self):
        from .. import __version__
        a = Adw.AboutDialog(application_name="Vifte", application_icon="no.vifte.Vifte",
                            developer_name="From Norway with Love ❤️",
                            developers=["Jan Ivar Sørensen"],
                            copyright="© 2026 Jan Ivar Sørensen",
                            website="https://github.com/Vindihaaret/vifte",
                            issue_url="https://github.com/Vindihaaret/vifte/issues",
                            version=__version__,
                            comments=_(
                                "Viftestyring for Linux.\nLeser og skriver konfigurasjonsfiler fra "
                                "FanControl (Rem0o) for Windows."),
                            license_type=Gtk.License.GPL_3_0)
        a.present(self)


class App(Adw.Application):
    def __init__(self, minimized=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.minimized = minimized
        self.tray = None
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, prov, 800)
        # ikonene følger med pakken, så de finnes også når programmet kjøres fra kildekoden
        Gtk.IconTheme.get_for_display(display).add_search_path(ICON_DIR)
        Gtk.Window.set_default_icon_name(APP_ID)
        try:
            self.tray = Tray(self.show_window, "no.vifte.Vifte-symbolic", ICON_DIR)
            self.hold()  # fortsett å kjøre i statusfeltet når vinduet lukkes
        except Exception as ex:
            print("statusfelt utilgjengelig:", ex, file=sys.stderr)
            self.tray = None

    def show_window(self):
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self)
            if self.minimized and self.tray:
                self.minimized = False
                return  # bare ikon i statusfeltet; vinduet vises fra menyen der
        self.window.present()

    def quit_all(self):
        if self.tray:
            self.release()
        self.quit()


def main(minimized=False):
    return App(minimized).run(sys.argv[:1])
