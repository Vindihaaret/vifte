"""Hovedvinduet."""
import os
import shutil
import sys
import time
from collections import deque

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from ..client import Client  # noqa: E402
from ..config import (CURVE_TITLES, CURVE_TYPES, SENSOR_TYPES, curve_type,  # noqa: E402
                      custom_sensor_type, new_curve, new_custom_sensor, parse_point)
from .. import __version__, debuglog, i18n  # noqa: E402
from ..i18n import LANGUAGES, _, language_setting, set_language_setting  # noqa: E402
from .alerts import AlertMonitor  # noqa: E402
from .common import (Model, StatusHero, combo_row, move_entry, move_group, spin_row, stress_path,  # noqa: E402
                     switch_row)
from .dialogs import (ControlDialog, CurveEditor, CustomSensorDialog, PromptDialog, RemapDialog,  # noqa: E402
                      RenameDialog)
from .graph import HistoryGraph, MiniCurve, Sparkline  # noqa: E402
from .look import (board_choice, board_sensors, disk_selection, disks, fan_summary, group_kind,  # noqa: E402
                   heat_step, HISTORY_RANGES, history_ticks, LIVE_KEEP, merge_history, PALETTE, palette_css,
                   safety_state, temp_status)
from .tray import Tray  # noqa: E402
from .update import UpdateManager, auto_enabled, last_checked  # noqa: E402

APP_ID = "no.vifte.Vifte"
UNINSTALLER = "/usr/local/bin/vifte-uninstall"
ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "icons")
AUTOSTART_SYSTEM = "/etc/xdg/autostart/no.vifte.Vifte-tray.desktop"
STRESS_URL = "https://github.com/Vindihaaret/stress"

STYLE_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")


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


def themed_image(names, **kw):
    """Ikon med reserver: det første navnet temaet har (Adwaita mangler f.eks. et CPU-ikon)."""
    return Gtk.Image(gicon=Gio.ThemedIcon.new_from_names(names), **kw)


ICONS = {"cpu": ["cpu-symbolic", "computer-chip-symbolic", "computer-symbolic"],
         "gpu": ["graphics-card-symbolic", "video-display-symbolic"],
         "board": ["computer-chip-symbolic", "auth-smartcard-symbolic", "computer-symbolic"],
         "disk": ["drive-harddisk-solidstate-symbolic", "drive-harddisk-symbolic"],
         "fan": ["no.vifte.Vifte-symbolic"],
         "other": ["computer-symbolic"],
         "custom": ["starred-symbolic"]}

# gruppe på Kontroller/Hastigheter/Sensorer → (ikon, ikonflate, sparkline for temperatur, sparkline for turtall).
# Vifter er marineblå, unntatt på skjermkortet (kobber).
GROUP_STYLE = {"cpu": ("cpu", "cpu", "cpu", "cpu"), "gpu": ("gpu", "gpu", "gpu", "gpu"),
               "board": ("board", "neutral", "neutral", "cpu"), "disk": ("disk", "neutral", "neutral", "cpu"),
               "other": ("other", "neutral", "neutral", "cpu"), "custom": ("custom", "accent", "accent", "accent")}


def icon_surface(icon, kind="neutral", small=False):
    """Liten farget flate med hvitt ikon: CPU marineblå, GPU kobber, ellers nøytral grå."""
    img = themed_image(ICONS[icon], pixel_size=13 if small else 14, valign=Gtk.Align.CENTER)
    img.set_css_classes(["vifte-icon-surface", kind] + (["small"] if small else []))
    return img


def link_button(label, cb):
    """«Kontroller ›»-lenken øverst til høyre i kortene på Oversikt."""
    b = Gtk.Button(css_classes=["flat", "vifte-link"], valign=Gtk.Align.CENTER)
    box = Gtk.Box(spacing=4)
    box.append(Gtk.Label(label=label))
    box.append(Gtk.Image(icon_name="pan-end-symbolic", pixel_size=12))
    b.set_child(box)
    b.connect("clicked", lambda *_a: cb())
    return b


def accent_supported():
    """Skrivebordets aksentfarge finnes fra libadwaita 1.6 (GNOME 47)."""
    return (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)


class StatusChip(Gtk.Box):
    """«● Rolig»-merket. Statusfargen står alltid sammen med tekst."""
    def __init__(self):
        super().__init__(spacing=5, visible=False, valign=Gtk.Align.CENTER, css_classes=["vifte-chip"])
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.label = Gtk.Label()
        self.append(self.dot)
        self.append(self.label)

    def set_status(self, name, label):
        self.set_visible(bool(name))
        self.dot.set_css_classes(["vifte-dot"] + ([name] if name else []))
        self.label.set_label(label)


class TempCard(Gtk.Box):
    """Temperaturkort på Oversikt: ikon og navn, status, stort tall med sparkline og sensornavn under.
    Med menu=True får kortet en ⋯-knapp for å velge sensor. set_rows() viser en liste i stedet
    for det store tallet («Alle disker»)."""
    def __init__(self, title, icon, kind="neutral", menu=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True,
                         css_classes=["card", "vifte-card", "vifte-temp-card"])
        head = Gtk.Box(spacing=6)
        head.append(icon_surface(icon, kind))
        head.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=3, tooltip_text=title,
                              css_classes=["heading"]))
        self.chip = StatusChip()
        head.append(self.chip)
        self.menu = None
        if menu:
            self.menu = Gtk.MenuButton(icon_name="view-more-horizontal-symbolic", valign=Gtk.Align.CENTER,
                                       css_classes=["flat", "vifte-more"], tooltip_text=_("Velg sensor"))
            head.append(self.menu)
        self.append(head)
        self.body = Gtk.Box(spacing=10)
        self.value = Gtk.Label(label="–", valign=Gtk.Align.END, css_classes=["vifte-big"])
        self.unit = Gtk.Label(valign=Gtk.Align.END, margin_bottom=5, css_classes=["vifte-unit"])
        self.spark = Sparkline(color=kind, dot=True, height=34)
        self.spark.set_valign(Gtk.Align.END)
        number = Gtk.Box(spacing=3)
        number.append(self.value)
        number.append(self.unit)
        self.body.append(number)
        self.body.append(self.spark)
        self.append(self.body)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False)
        self.append(self.rows)
        # liten naturlig bredde, så fire kort får plass på én rad; teksten kuttes bare når plassen mangler
        self.sub = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=12, css_classes=["caption", "dim-label"])
        self.append(self.sub)

    def set_temp(self, text):
        """«41.8 °C» → stort tall og mindre enhet."""
        number, _sp, unit = text.rpartition(" ")
        self.value.set_label(number or text)
        self.unit.set_label(unit if number else "")

    def set_rows(self, rows):
        """rows: [(kort navn, fullt navn, status, verdi)] eller None for vanlig visning."""
        self.body.set_visible(rows is None)
        self.rows.set_visible(rows is not None)
        if rows is None:
            return
        while len(list(self.rows)) > len(rows):
            self.rows.remove(self.rows.get_last_child())
        while len(list(self.rows)) < len(rows):
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Box(valign=Gtk.Align.CENTER))
            row.append(Gtk.Label(xalign=0, hexpand=True, ellipsize=3))
            row.append(Gtk.Label(css_classes=["vifte-sub", "heading"]))
            self.rows.append(row)
        for row, (short, full, status, value) in zip(self.rows, rows):
            dot, name, val = list(row)
            dot.set_css_classes(["vifte-dot"] + ([status] if status else []))
            name.set_label(short)
            name.set_tooltip_text(full)
            val.set_label(value)


class CurvesPage(Gtk.Box):
    """Kurver: liste med søk til venstre, detaljen for valgt kurve til høyre (under hverandre i smale vinduer)."""
    def __init__(self):
        super().__init__(css_classes=["vifte-curves"])
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                       margin_top=4, margin_bottom=12)
        side.set_size_request(280, -1)
        self.search = Gtk.SearchEntry(placeholder_text=_("Søk i kurver"))
        side.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["vifte-curve-list"])
        self.list.set_filter_func(self._filter)
        self.search.connect("changed", lambda *_a: self.list.invalidate_filter())   # kort liste: filtrer straks
        # i smale vinduer (listen over detaljen) får listen sin naturlige høyde, høyst max_content_height
        self.list_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                              propagate_natural_height=True, max_content_height=260,
                                              child=self.list)
        side.append(self.list_scroll)
        self.append(side)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, css_classes=["vifte-curves-sep"]))
        self.detail = Adw.Bin()
        self.detail_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
        self.detail_scroll.set_child(Adw.Clamp(maximum_size=1000, tightening_threshold=1000, margin_start=16,
                                               margin_end=12, margin_top=4, margin_bottom=24, child=self.detail))
        self.append(self.detail_scroll)

    def _filter(self, row):
        q = self.search.get_text().strip().lower()
        return not q or q in getattr(row, "search_text", "")

    def clear(self):
        while (r := self.list.get_first_child()):
            self.list.remove(r)
        self.detail.set_child(None)


class Section(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12,
                           margin_bottom=24, margin_start=12, margin_end=12)
        clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=1400, child=self.box)
        self.set_child(clamp)
        self.header = None
        # Rull automatisk når et kort eller en gruppe dras nær toppen eller bunnen.
        self._scroll_speed = 0.0
        self._scroll_timer = None
        self._dnd = Gtk.DropControllerMotion()
        self._dnd.connect("motion", self._dnd_motion)
        self._dnd.connect("leave", lambda *_a: self._set_scroll(0))
        self.add_controller(self._dnd)

    EDGE = 80     # px fra kanten der rullingen starter
    MAX_STEP = 28  # px per tikk ved kanten

    def _dnd_motion(self, _c, _x, y):
        h = self.get_height()
        if y < self.EDGE:
            self._set_scroll(-self.MAX_STEP * (self.EDGE - y) / self.EDGE)
        elif y > h - self.EDGE:
            self._set_scroll(self.MAX_STEP * (y - (h - self.EDGE)) / self.EDGE)
        else:
            self._set_scroll(0)

    def _set_scroll(self, speed):
        self._scroll_speed = speed
        if speed and self._scroll_timer is None:
            self._scroll_timer = GLib.timeout_add(16, self._scroll_tick)

    def _scroll_tick(self):
        if not self._scroll_speed or self._dnd.get_drop() is None:
            self._scroll_speed, self._scroll_timer = 0, None
            return False
        adj = self.get_vadjustment()
        adj.set_value(max(adj.get_lower(), min(adj.get_upper() - adj.get_page_size(),
                                               adj.get_value() + self._scroll_speed)))
        return True

    def clear(self):
        while (c := self.box.get_first_child()):
            self.box.remove(c)

    def new_list(self, title, kind="other", movable=False):
        """Gruppe som innfelt liste: overskrift (draghåndtak, ikonflate, tittel) og en Gtk.ListBox.
        Klikk på en rad kaller radens on_open, hvis den har en."""
        head = Gtk.Box(spacing=8, margin_top=6)
        if movable:
            head.set_tooltip_text(_("Dra for å flytte gruppen"))
            head.append(Gtk.Image(icon_name="list-drag-handle-symbolic", opacity=0.6))
            head.set_cursor(Gdk.Cursor.new_from_name("grab"))
        icon, surface, _t, _f = GROUP_STYLE[kind]
        head.append(icon_surface(icon, surface, small=True))
        head.append(Gtk.Label(label=title, xalign=0, ellipsize=3, css_classes=["vifte-group-label"]))
        self.header = head
        self.box.append(head)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list", "vifte-list"])
        lst.connect("row-activated", lambda _l, row: getattr(row, "on_open", None) and row.on_open())
        self.box.append(lst)
        return lst


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Vifte", default_width=1400, default_height=840)
        self.model = Model(Client())
        self.live_dialogs = set()
        self.updaters = []
        self.connected = False
        self._structure_key = None
        self.alerts = AlertMonitor()
        self._history_at = 0.0

        self.toasts = Adw.ToastOverlay()
        self.toasts.add_css_class("vifte-root")  # bærer bakgrunnen; fargetonen ligger på main_view oppå
        # sidefelt til venstre, sidene til høyre; kollapser til ett felt om gangen i smale vinduer
        self.split = Adw.NavigationSplitView(min_sidebar_width=232, max_sidebar_width=232)
        self.split.add_css_class("vifte-main")
        self.main_view = self.split
        self._tint = None
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar(css_classes=["vifte-page-header"])
        header.set_title_widget(Gtk.Box())   # tittelen står til venstre, som i sidehodet i designet
        tv.add_top_bar(header)
        self.stack = Adw.ViewStack()
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, margin_start=8)
        self.page_title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["vifte-page-title"])
        self.page_subtitle = Gtk.Label(xalign=0, ellipsize=3, css_classes=["vifte-page-subtitle", "dim-label"])
        titles.append(self.page_title)
        titles.append(self.page_subtitle)
        header.pack_start(titles)

        self.profile_btn = Gtk.MenuButton(tooltip_text=_("Konfigurasjon (profil)"), hexpand=True,
                                          css_classes=["vifte-config"])
        # navnet til venstre og pilen til høyre, som i designet
        self.profile_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        child = Gtk.Box(spacing=6)
        child.append(self.profile_label)
        child.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.profile_btn.set_child(child)

        # høyre: meny og lagre (pause ligger i Beskyttelse-kortet og i menyen)
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
        self.stress_menu = Gio.Menu()
        self.refresh_stress_menu()
        menu.append_section(None, self.stress_menu)
        sec = Gio.Menu()
        sec.append(_("Vis skjulte kort"), "win.show-hidden")
        sec.append(_("Pause styring (BIOS overtar)"), "win.pause")
        sec.append(_("Innstillinger …"), "win.settings")
        sec.append(_("Se etter oppdateringer …"), "win.check-update")
        sec.append(_("Om Vifte"), "win.about")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append(_("Avslutt"), "win.quit")
        menu.append_section(None, sec)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Meny"))
        mb.connect("notify::active", lambda b, _p: b.get_active() and self.refresh_stress_menu())
        self.save_btn = Gtk.Button(label=_("Lagre"), tooltip_text=_("Lagre konfigurasjonen (Ctrl+S)"))
        self.save_btn.connect("clicked", lambda *_: self.save())
        for b in (self.save_btn, mb):
            b.set_valign(Gtk.Align.CENTER)   # hodet er 72 px høyt
        # «Ny sensor» står i sidehodet og vises bare på Sensorer
        self.add_sensor_btn = Gtk.MenuButton(always_show_arrow=True, valign=Gtk.Align.CENTER, visible=False)
        add = Gtk.Box(spacing=6)
        add.append(Gtk.Image(icon_name="list-add-symbolic"))
        self.add_sensor_label = Gtk.Label(label=_("Ny sensor"))
        add.append(self.add_sensor_label)
        self.add_sensor_btn.set_child(add)
        sensor_menu = Gio.Menu()
        for k, t in SENSOR_TYPES.items():
            sensor_menu.append(_(t), f"win.add-sensor::{k}")
        self.add_sensor_btn.set_menu_model(sensor_menu)
        a = Gio.SimpleAction.new("add-sensor", GLib.VariantType.new("s"))
        a.connect("activate", lambda _a, p: self.add_sensor(p.get_string()))
        self.add_action(a)
        header.pack_end(self.save_btn)
        header.pack_end(mb)
        header.pack_end(self.add_sensor_btn)
        self.add_curve_btn = Gtk.MenuButton(always_show_arrow=True, valign=Gtk.Align.CENTER, visible=False)
        add = Gtk.Box(spacing=6)
        add.append(Gtk.Image(icon_name="list-add-symbolic"))
        self.add_curve_label = Gtk.Label(label=_("Ny kurve"))
        add.append(self.add_curve_label)
        self.add_curve_btn.set_child(add)
        curve_menu = Gio.Menu()
        for k in CURVE_TYPES:
            curve_menu.append(_(CURVE_TITLES[k]), f"win.add-curve::{k}")
        self.add_curve_btn.set_menu_model(curve_menu)
        a = Gio.SimpleAction.new("add-curve", GLib.VariantType.new("s"))
        a.connect("activate", lambda _a, p: self.add_curve(p.get_string()))
        self.add_action(a)
        header.pack_end(self.add_curve_btn)

        for name, cb in (("import", self.import_config), ("export", self.export_config),
                         ("new-config", self.new_config), ("save-as", self.save_as),
                         ("delete-config", self.delete_config), ("pair", self.pair),
                         ("remap", lambda: self.open_dialog(RemapDialog)), ("rescan", self.rescan),
                         ("settings", self.settings), ("about", self.about), ("uninstall", self.uninstall),
                         ("check-update", lambda: self.get_application().updates.check(manual=True)),
                         ("stress", self.open_stress),
                         ("quit", self.request_quit)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
        for name, cb in (("show-hidden", self.toggle_hidden),
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
        pages = (("overview", _("Oversikt"), "go-home-symbolic"),
                 ("controls", _("Kontroller"), "preferences-system-symbolic"),
                 ("curves", _("Kurver"), "power-profile-performance-symbolic"),
                 ("speeds", _("Hastigheter"), "weather-windy-symbolic"),
                 ("sensors", _("Sensorer"), "temperature-symbolic"))
        for key, title, icon in pages:
            sec = self.build_overview() if key == "overview" else CurvesPage() if key == "curves" else Section()
            if key != "overview":
                self.pages[key] = sec
            self.stack.add_titled_with_icon(sec, key, title, icon)
        tv.set_content(self.stack)
        self.curve_selected = None
        self.live_history = deque(maxlen=LIVE_KEEP)   # [(tid, {sensor-id: °C})] hvert sekund, for grafen på Oversikt
        self._curve_building = False
        self.pages["curves"].list.connect(
            "row-selected", lambda _l, row: row is not None and not self._curve_building and self.show_curve(row.name))
        self.content_page = Adw.NavigationPage(title=pages[0][1], tag="content", child=tv)
        self.split.set_content(self.content_page)
        self.split.set_sidebar(Adw.NavigationPage(title="Vifte", tag="sidebar", child=self.build_sidebar(pages)))
        self.stack.connect("notify::visible-child-name", lambda *_a: self.page_changed())
        self.narrow_hide = []
        self.split.connect("notify::collapsed", lambda *_a: self.apply_narrow())
        self.page_changed()

        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_a: self.apply_style_mode())
        style.connect("notify::high-contrast", lambda *_a: self.apply_style_mode())
        self.apply_style_mode()

        # middels bredt: kortene på Oversikt to og to; smalt: i tillegg sidefelt og side hver for seg.
        # Bare det sist lagt til av breakpointene som passer gjelder, så det smale har alle setterne.
        for width, collapse in ((1320, False), (700, True)):
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {width}sp"))
            for box in (self.overview_cards, self.overview_bottom):
                bp.add_setter(box, "orientation", Gtk.Orientation.VERTICAL)
            bp.add_setter(self.overview_bottom, "homogeneous", False)
            if collapse:
                bp.add_setter(self.split, "collapsed", True)
                bp.add_setter(self.pages["curves"], "orientation", Gtk.Orientation.VERTICAL)
                bp.add_setter(self.pages["curves"].list_scroll, "vexpand", False)
                for label in (self.add_sensor_label, self.add_curve_label):   # bare ikonet, så tittelen får plass
                    bp.add_setter(label, "visible", False)
            self.add_breakpoint(bp)

        self.connect("close-request", self.on_close)
        self.poll()
        self._timer = GLib.timeout_add(1000, self.poll)

    def apply_style_mode(self):
        """Lys/mørk velger variant av glasset; i høykontrast er glasset av (vanlige Adwaita-kort)."""
        sm = Adw.StyleManager.get_default()
        for name, on in (("vifte-light", not sm.get_dark()), ("vifte-glass", not sm.get_high_contrast())):
            if on:
                self.toasts.add_css_class(name)
            else:
                self.toasts.remove_css_class(name)

    # ================================================================ sidefelt og sidehode
    def build_sidebar(self, pages):
        """App-navn, konfigurasjonsvelger, sidene, statuskort og Innstillinger."""
        tv = Adw.ToolbarView(css_classes=["vifte-sidebar"])
        header = Adw.HeaderBar(css_classes=["vifte-page-header"])
        header.set_title_widget(Gtk.Box())
        tv.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=10, margin_end=10, margin_bottom=12)
        # app-navnet står under vindusknappene, ikke ved siden av dem
        brand = Gtk.Box(spacing=10, margin_start=6, margin_bottom=16)
        brand.append(Gtk.Image(icon_name="no.vifte.Vifte", pixel_size=36))
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        names.append(Gtk.Label(label="Vifte", xalign=0, css_classes=["heading"]))
        names.append(Gtk.Label(label="FanControl for Linux", xalign=0, css_classes=["caption", "dim-label"]))
        brand.append(names)
        box.append(brand)
        box.append(Gtk.Label(label=_("Konfigurasjon"), xalign=0, margin_start=6, margin_bottom=6,
                             css_classes=["caption-heading", "dim-label"]))
        box.append(self.profile_btn)

        self.nav = Gtk.ListBox(css_classes=["navigation-sidebar", "vifte-nav"], margin_top=12)
        self.nav_rows = {}
        for key, title, icon in pages:
            row_box = Gtk.Box(spacing=12)
            row_box.append(Gtk.Image(icon_name=icon))
            row_box.append(Gtk.Label(label=title, xalign=0))
            row = Gtk.ListBoxRow(child=row_box)
            row.key = key
            self.nav_rows[key] = row
            self.nav.append(row)
        self.nav.connect("row-activated", lambda _l, row: self.show_page(row.key))
        box.append(self.nav)
        box.append(Gtk.Box(vexpand=True))

        # statuskort: det viktigste om sikkerheten; klikk åpner Innstillinger › Sikkerhet
        self.status_btn = Gtk.Button(css_classes=["vifte-status"], margin_bottom=6)
        status = Gtk.Box(spacing=10)
        self.status_icon = Gtk.Image(icon_name="emblem-ok-symbolic", valign=Gtk.Align.CENTER,
                                     css_classes=["vifte-status-icon"])
        status.append(self.status_icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.status_title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["vifte-status-title"])
        self.status_text = Gtk.Label(xalign=0, wrap=True, css_classes=["caption", "dim-label"])   # smalt sidefelt: bryt linjen
        texts.append(self.status_title)
        texts.append(self.status_text)
        status.append(texts)
        self.status_btn.set_child(status)
        self.status_btn.connect("clicked", lambda *_a: self.settings("safety"))
        box.append(self.status_btn)

        settings = Gtk.ListBox(css_classes=["navigation-sidebar", "vifte-nav"], selection_mode=Gtk.SelectionMode.NONE)
        row_box = Gtk.Box(spacing=12)
        row_box.append(Gtk.Image(icon_name="emblem-system-symbolic"))
        row_box.append(Gtk.Label(label=_("Innstillinger"), xalign=0))
        settings.append(Gtk.ListBoxRow(child=row_box))
        settings.connect("row-activated", lambda *_a: self.settings())
        box.append(settings)
        tv.set_content(box)
        return tv

    def apply_narrow(self):
        """I smale vinduer (sidefeltet kollapset) skjules det som er minst viktig i radene."""
        for w in self.narrow_hide:
            w.set_visible(not self.split.get_collapsed())

    def show_page(self, key):
        self.stack.set_visible_child_name(key)
        self.split.set_show_content(True)

    def page_changed(self):
        """Markeringen i sidefeltet og sidehodet følger siden, også når koden bytter side."""
        key = self.stack.get_visible_child_name()
        row = self.nav_rows.get(key)
        if row is not None and self.nav.get_selected_row() is not row:
            self.nav.select_row(row)
        child = self.stack.get_visible_child()
        title = self.stack.get_page(child).get_title() if child else ""
        self.add_sensor_btn.set_visible(key == "sensors")
        self.add_curve_btn.set_visible(key == "curves")
        self.page_title.set_label(title)
        self.content_page.set_title(title)
        self.update_page_subtitle()

    def update_page_subtitle(self):
        m, key = self.model, self.stack.get_visible_child_name()
        cfg = m.cfg
        if key == "controls":
            _avg, fans, _pumps = fan_summary(m.state.get("control_state") or {})
            text = _("{} vifter · {} styrt av Vifte").format(len([e for e in cfg.controls if self.visible(e)]),
                                                             fans)
        elif key == "curves":
            used = {(e.get("SelectedFanCurve") or {}).get("Name") for e in cfg.controls if e.get("Enable")}
            used.discard(None)
            text = _("{} kurver · {} i bruk").format(len(cfg.curves), len(used))
        elif key == "speeds":
            text = _("Turtall fra alle viftesensorer")
        elif key == "overview":
            text = _("Temperaturer og vifter akkurat nå")
        else:
            text = _("Historikk for siste 30 minutter")
        self.page_subtitle.set_label(text)

    def update_sidebar_status(self):
        st = self.model.state
        state = safety_state(st)
        _avg, fans, _pumps = fan_summary(st.get("control_state") or {})
        self.status_title.set_label({"emergency": _("Nødmodus"), "sensor_lost": _("Sensor borte"),
                                     "paused": _("Styring på pause"), "ok": _("Alt i orden"),
                                     "no_emergency": _("Nødmodus av")}[state])
        self.status_text.set_label(_("Tjenesten styrer {} vifter").format(fans))
        self.status_icon.set_from_icon_name({"ok": "emblem-ok-symbolic", "paused": "media-playback-pause-symbolic"}
                                            .get(state, "dialog-warning-symbolic"))
        level = {"ok": "calm", "emergency": "emergency"}.get(state, "warm")
        self.status_icon.set_css_classes(["vifte-status-icon", level])

    # ================================================================ oversikt og fargetone
    def build_overview(self):
        """Oversikt: fire temperaturkort, historikkgraf og kortene Vifter og Beskyttelse."""
        page = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=4, margin_bottom=24,
                      margin_start=12, margin_end=12)
        page.set_child(Adw.Clamp(maximum_size=1400, tightening_threshold=1400, child=box))

        self.card_cpu = TempCard("CPU", "cpu", "cpu")
        self.card_gpu = TempCard("GPU", "gpu", "gpu")
        self.card_board = TempCard(_("Hovedkort"), "board", menu=True)
        self.card_disk = TempCard(_("Lagring"), "disk", menu=True)
        self.card_board.menu.set_create_popup_func(self.fill_board_menu)
        self.card_disk.menu.set_create_popup_func(self.fill_disk_menu)
        # fire på rad; i smale vinduer to og to (breakpointet snur self.overview_cards)
        self.overview_cards = Gtk.Box(spacing=14, homogeneous=True)
        for pair in ((self.card_cpu, self.card_gpu), (self.card_board, self.card_disk)):
            row = Gtk.Box(spacing=14, homogeneous=True)
            for card in pair:
                row.append(card)
            self.overview_cards.append(row)
        box.append(self.overview_cards)

        # historikk for CPU og GPU
        hist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["card", "vifte-card"])
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label=_("Temperatur"), css_classes=["heading"]))
        self.history_span = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        head.append(self.history_span)
        for kind, name in (("cpu", "CPU"), ("gpu", "GPU")):
            item = Gtk.Box(spacing=6, margin_start=10)
            item.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["vifte-legend", kind]))
            item.append(Gtk.Label(label=name, css_classes=["caption"]))
            head.append(item)
        # små valg for tidsrommet; grafen og tidsaksen følger med
        ranges = Gtk.Box(margin_start=14, valign=Gtk.Align.CENTER, css_classes=["linked", "vifte-range"])
        first = None
        for minutes in HISTORY_RANGES:
            b = Gtk.ToggleButton(label=_("{} min").format(minutes), group=first)
            first = first or b
            b.set_active(minutes == self.history_minutes())
            b.connect("toggled", lambda btn, n=minutes: btn.get_active() and self.set_history_range(n))
            ranges.append(b)
        head.append(ranges)
        hist.append(head)
        self.history_graph = HistoryGraph(height=180)
        hist.append(self.history_graph)
        self.set_history_range(self.history_minutes())
        box.append(hist)

        # nederst: Vifter og Beskyttelse
        self.overview_bottom = Gtk.Box(spacing=14, homogeneous=True)
        fans = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card", "vifte-card", "vifte-bottom-card"])
        head = Gtk.Box()
        head.append(Gtk.Label(label=_("Vifter"), xalign=0, hexpand=True, css_classes=["heading"]))
        head.append(link_button(_("Kontroller"), lambda: self.show_page("controls")))
        fans.append(head)
        summary = Gtk.Box(spacing=6, css_classes=["vifte-summary"])
        self.fans_avg = Gtk.Label(label="–", valign=Gtk.Align.CENTER, css_classes=["vifte-big", "vifte-summary-value"])
        summary.append(self.fans_avg)
        summary.append(Gtk.Label(label="%", valign=Gtk.Align.CENTER, margin_top=6, css_classes=["vifte-unit"]))
        self.fans_sub = Gtk.Label(xalign=0, hexpand=True, ellipsize=3, margin_start=8, valign=Gtk.Align.CENTER,
                                  css_classes=["caption", "dim-label"])
        summary.append(self.fans_sub)
        fans.append(summary)
        self.overview_fans = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        fans.append(self.overview_fans)
        self.overview_bottom.append(fans)

        safety = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["card", "vifte-card", "vifte-bottom-card"])
        head = Gtk.Box()
        head.append(Gtk.Label(label=_("Beskyttelse"), xalign=0, hexpand=True, css_classes=["heading"]))
        head.append(link_button(_("Innstillinger"), lambda: self.settings("safety")))
        safety.append(head)
        summary = Gtk.Box(spacing=10, css_classes=["vifte-summary"])
        self.safety_icon = Gtk.Image(icon_name="security-high-symbolic", valign=Gtk.Align.CENTER,
                                     css_classes=["vifte-status-icon", "calm"])
        summary.append(self.safety_icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.safety_title = Gtk.Label(xalign=0, ellipsize=3, css_classes=["heading"])
        self.safety_sub = Gtk.Label(xalign=0, ellipsize=3, css_classes=["caption", "dim-label"])
        texts.append(self.safety_title)
        texts.append(self.safety_sub)
        summary.append(texts)
        safety.append(summary)
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["vifte-overview-rows"])

        def row(title, control):
            r = Gtk.Box(spacing=12, css_classes=["vifte-overview-row"])
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            texts.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
            sub = Gtk.Label(xalign=0, ellipsize=3, css_classes=["caption", "dim-label"])
            texts.append(sub)
            r.append(texts)
            control.set_valign(Gtk.Align.CENTER)
            r.append(control)
            rows.append(r)
            return sub
        # samme innstillinger som i Innstillinger › Sikkerhet; ikke send noe når bryterne oppdateres herfra
        self._ov_syncing = False
        self.ov_failsafe, self.ov_lost = Gtk.Switch(), Gtk.Switch()
        for sw, key in ((self.ov_failsafe, "failsafe_enabled"), (self.ov_lost, "sensor_lost_enabled")):
            sw.connect("notify::active",
                       lambda w, _p, k=key: self._ov_syncing or self.call("settings", **{k: w.get_active()}))
        self.ov_failsafe_sub = row(_("Nødmodus"), self.ov_failsafe)
        self.ov_lost_sub = row(_("Sikring ved sensortap"), self.ov_lost)
        self.ov_pause = Gtk.Button(action_name="win.pause")
        pause = Gtk.Box(spacing=6)
        self.ov_pause_icon = Gtk.Image(icon_name="media-playback-pause-symbolic")
        self.ov_pause_label = Gtk.Label(label=_("Pause"))
        pause.append(self.ov_pause_icon)
        pause.append(self.ov_pause_label)
        self.ov_pause.set_child(pause)
        self.ov_control_sub = row(_("Styring"), self.ov_pause)
        safety.append(rows)
        self.overview_bottom.append(safety)
        box.append(self.overview_bottom)
        return page

    def build_overview_fans(self):
        """Nøytral liste over viftene på Oversikt – samme kontroller som på Kontroller-siden."""
        m, cfg = self.model, self.model.cfg
        box = self.overview_fans
        while (c := box.get_first_child()):
            box.remove(c)
        for e in cfg.controls:
            if not self.visible(e):
                continue
            ident = e.get("Identifier")
            row = Gtk.Box(spacing=12, css_classes=["vifte-fan-row"])
            row.append(icon_surface("fan"))
            row.append(Gtk.Label(label=e.get("NickName") or e.get("Name") or ident, xalign=0, hexpand=True,
                                 ellipsize=3, css_classes=["heading"]))
            rpm = Gtk.Label(xalign=1, css_classes=["caption", "dim-label", "vifte-sub"])
            level = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER)
            level.set_size_request(100, -1)
            pct = Gtk.Label(xalign=1, width_chars=5, css_classes=["heading", "vifte-sub"])
            for w in (rpm, level, pct):
                row.append(w)
            box.append(row)

            def update(row=row, rpm=rpm, level=level, pct=pct, ident=ident):
                st = (m.state.get("control_state") or {}).get(ident, {})
                row.set_visible(not st.get("no_fan"))
                out = st.get("out")
                val = out if out is not None else st.get("read")
                pct.set_label(f"{val:.0f} %" if val is not None else "–")
                level.set_value(val or 0)
                r = st.get("rpm")
                rpm.set_label(_("BIOS styrer") if out is None else f"{r:.0f} RPM" if r is not None else "")
                if out is None:
                    row.add_css_class("idle")   # styres ikke av Vifte: svakere tone
                else:
                    row.remove_css_class("idle")
            self.updaters.append(update)

    def choice_popover(self, items, current, on_pick):
        """Liste med hake ved valgt oppføring. items: [(nøkkel, tekst, undertekst eller None)];
        nøkkel None gir en skillelinje."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        for key, text, sub in items:
            if key is None:
                box.append(Gtk.Separator(margin_top=4, margin_bottom=4))
                continue
            b = Gtk.Button(css_classes=["flat"])
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Image(icon_name="object-select-symbolic", opacity=1 if key == current else 0))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            texts.append(Gtk.Label(label=text, xalign=0))
            if sub:
                texts.append(Gtk.Label(label=sub, xalign=0, css_classes=["caption", "dim-label"]))
            row.append(texts)
            b.set_child(row)
            b.connect("clicked", lambda _b, k=key: (pop.popdown(), on_pick(k), self.update_overview()))
            box.append(b)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    max_content_height=420, child=box)
        pop.set_child(scroll)
        return pop

    def fill_board_menu(self, button):
        from ..i18n import get_setting, set_setting
        sensors = self.model.state.get("sensors", [])
        # finnes ingen hovedkortsensor, kan hvilken som helst temperatursensor velges
        options = board_sensors(sensors) or [s for s in sensors if s["kind"] == "temperature"]
        items = [(s["id"], s["name"], s["hardware"]) for s in options]
        current = board_choice(sensors, get_setting("overview_board_sensor"))
        button.set_popover(self.choice_popover(items, current, lambda k: set_setting("overview_board_sensor", k)))

    def fill_disk_menu(self, button):
        from ..i18n import get_setting, set_setting
        sensors = self.model.state.get("sensors", [])
        dl = disks(sensors)
        mode, pick = get_setting("overview_disk_mode", "hottest"), get_setting("overview_disk_pick")
        if dl:
            items = [("hottest", _("Den varmeste disken"), None), ("all", _("Alle disker"), None), (None, None, None)]
            items += [("pick:" + d["id"], d["short"], d["full"]) for d in dl]
        else:
            items = [("pick:" + s["id"], s["name"], s["hardware"]) for s in sensors if s["kind"] == "temperature"]
        current = "pick:" + str(pick) if mode == "pick" else mode

        def chosen(key):
            if key.startswith("pick:"):
                set_setting("overview_disk_pick", key[5:])
                key = "pick"
            set_setting("overview_disk_mode", key)
        button.set_popover(self.choice_popover(items, current, chosen))

    def build_debug_settings(self, page, dlg):
        """Feilsøkingslogg som i Waypoint: bryter, mappen og «Kopier siste logg»."""
        from ..i18n import get_setting, set_setting
        g = Adw.PreferencesGroup(title=_("Feilsøking"))
        page.add(g)
        sw = Adw.SwitchRow(title=_("Feilsøkingslogg"), active=bool(get_setting("debug", False)),
                           subtitle=_("Skriver det Vifte gjør til en loggfil: kall til tjenesten, oppdateringer og "
                                      "feil. Nyttig når noe går galt. Loggene slettes etter {} dager.").format(
                               debuglog.KEEP_DAYS))
        sw.add_prefix(Gtk.Image(icon_name="utilities-terminal-symbolic"))

        def toggled(row, _p):
            set_setting("debug", row.get_active())
            debuglog.refresh()
            if row.get_active():
                debuglog.started("window")
                debuglog.info("debug log turned on")
        sw.connect("notify::active", toggled)
        g.add(sw)
        where = debuglog.log_dir().replace(GLib.get_home_dir(), "~", 1)
        row = Adw.ActionRow(title=_("Loggfiler"), subtitle=where, use_markup=False)

        def open_dir(*_a):
            os.makedirs(debuglog.log_dir(), exist_ok=True)
            Gtk.FileLauncher.new(Gio.File.new_for_path(debuglog.log_dir())).launch(self, None, None, None)

        def copy_last(*_a):
            last = debuglog.latest()
            if not last:
                dlg.add_toast(Adw.Toast(title=_("Ingen logger ennå")))
                return
            with open(last, encoding="utf-8", errors="replace") as f:
                text = f.read()[-2_000_000:]
            self.get_clipboard().set(text)
            dlg.add_toast(Adw.Toast(title=_("{} er kopiert").format(os.path.basename(last))))
        for label, func in ((_("Åpne mappen"), open_dir), (_("Kopier siste logg"), copy_last)):
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            b.connect("clicked", func)
            row.add_suffix(b)
        g.add(row)

    def set_system_accent(self, on):
        from ..i18n import set_setting
        set_setting("system_accent", on)
        self.get_application().apply_palette()

    def set_tint(self, on):
        from ..i18n import set_setting
        set_setting("tint_enabled", on)
        self.update_tint()

    def update_tint(self):
        """Svak fargetone etter den varmeste av CPU og GPU; rød i nødmodus."""
        from ..i18n import get_setting
        m = self.model
        temps = [v for v in (m.temp_value(self.auto_tray_sensor(k)) for k in ("cpu", "gpu")) if v is not None]
        emergency = bool((m.state.get("failsafe") or {}).get("active"))
        step = heat_step(max(temps) if temps else None, self._tint, emergency) \
            if get_setting("tint_enabled", False) else None
        if step != self._tint:
            if self._tint:
                self.main_view.remove_css_class(f"vifte-tint-{self._tint}")
            if step:
                self.main_view.add_css_class(f"vifte-tint-{step}")
            self._tint = step

    def history_minutes(self):
        from ..i18n import get_setting
        n = get_setting("overview_history_minutes", 30)
        return n if n in HISTORY_RANGES else 30

    def set_history_range(self, minutes):
        from ..i18n import get_setting, set_setting
        if get_setting("overview_history_minutes", 30) != minutes:
            set_setting("overview_history_minutes", minutes)
        self.history_span.set_label("· " + _("siste {} minutter").format(minutes))
        self.history_graph.minutes = minutes
        self.history_graph.x_labels = [_("{} min").format(f"−{n}") if n else _("Nå") for n in history_ticks(minutes)]
        self.history_graph.queue_draw()

    def update_overview(self):
        from ..i18n import get_setting
        m, st = self.model, self.model.state
        fs = st.get("failsafe") or {}
        emergency, limit = bool(fs.get("active")), fs.get("temp") or 95
        names = {"calm": _("Rolig"), "warm": _("Varm"), "hot": _("Het"), "emergency": _("Nødmodus")}
        sensors = st.get("sensors", [])
        by_id = {s["id"]: s for s in sensors}

        def status(v):
            return temp_status(v, emergency and v is not None and v >= limit)

        def show(card, ident, sub, tooltip=None):
            v = m.temp_value(ident) if ident else None
            card.set_rows(None)
            card.set_temp(m.fmt_temp(v))
            card.chip.set_status(status(v), names.get(status(v), ""))
            card.spark.set_values(m.history_values(ident)[1] if ident else [])
            card.sub.set_label(sub)
            card.sub.set_tooltip_text(tooltip or sub)
            card.sub.set_visible(True)

        def sensor_text(s):
            if not s:
                return _("Ingen sensor funnet")
            return s["hardware"] if s["name"].upper() in ("CPU", "GPU") else f"{s['hardware']} · {s['name']}"
        for kind, card in (("cpu", self.card_cpu), ("gpu", self.card_gpu)):
            ident = self.auto_tray_sensor(kind)
            show(card, ident, sensor_text(by_id.get(ident)))
        ident = board_choice(sensors, get_setting("overview_board_sensor"))
        show(self.card_board, ident, sensor_text(by_id.get(ident)))

        # lagring: den varmeste disken, alle disker eller én bestemt
        dl = disks(sensors)
        mode, pick = get_setting("overview_disk_mode", "hottest"), get_setting("overview_disk_pick")
        values = {d["id"]: m.temp_value(d["id"]) for d in dl}
        chosen = disk_selection(dl, values, mode, pick)
        if not dl:
            ident = pick if mode == "pick" and pick in by_id else None
            show(self.card_disk, ident, sensor_text(by_id.get(ident)))
        elif mode == "all" and len(chosen) > 1:
            self.card_disk.set_rows([(d["short"], d["full"], status(values[d["id"]]), m.fmt_temp(values[d["id"]]))
                                     for d in chosen])
            hottest = max((v for v in values.values() if v is not None), default=None)
            self.card_disk.chip.set_status(status(hottest), names.get(status(hottest), ""))
            self.card_disk.sub.set_visible(False)
        else:
            d = chosen[0]
            sub = d["short"] + (" · " + _("varmest av {}").format(len(dl)) if mode == "hottest" and len(dl) > 1 else "")
            show(self.card_disk, d["id"], sub, d["full"])

        # historikk: tjenestens punkter (hvert 5. s) flettet med vinduets egne sekundmålinger
        idents = {kind: ident for kind in ("cpu", "gpu") if (ident := self.auto_tray_sensor(kind))}
        now = round(time.time())
        if not self.live_history or self.live_history[-1][0] < now:
            self.live_history.append((now, {i: m.temp_value(i) for i in idents.values()}))
        t, daemon = [], {}
        for kind, ident in idents.items():
            t, daemon[kind] = m.history_values(ident)
        live_t = [x for x, _v in self.live_history]
        live = {kind: [v.get(ident) for _x, v in self.live_history] for kind, ident in idents.items()}
        t, merged = merge_history(t, daemon, live_t, live)
        series = [(kind, merged[kind]) for kind in idents]
        if m.fahrenheit:
            self.history_graph.fmt_y = lambda v: f"{v * 9 / 5 + 32:.0f} °F"
        else:
            self.history_graph.fmt_y = lambda v: f"{v:.0f} °C"
        self.history_graph.set_data(t, series, limit if fs.get("enabled") else None)

        # vifter
        avg, fans, pumps = fan_summary(st.get("control_state") or {})
        self.fans_avg.set_label(f"{avg:.0f}" if avg is not None else "–")
        self.fans_sub.set_label(" · ".join([_("snitt av styrte vifter"), _("{} styrt").format(fans)] +
                                           ([_("Pumper: {}").format(pumps)] if pumps else [])))

        # beskyttelse
        state = safety_state(st)
        self.safety_title.set_label({"emergency": _("Nødmodus"), "sensor_lost": _("Sensor borte"),
                                     "paused": _("Styring på pause"), "ok": _("Alt i orden"),
                                     "no_emergency": _("Nødmodus av")}[state])
        self.safety_sub.set_label({"emergency": _("Alle styrte vifter går på 100 %"),
                                   "sensor_lost": _("Viftene går på sikker hastighet"),
                                   "paused": _("BIOS styrer viftene"),
                                   "no_emergency": _("Kan slås på i innstillingene")}.get(
            state, _("Nødmodus ved {}").format(m.fmt_temp(limit))))
        self.safety_icon.set_from_icon_name("security-high-symbolic" if state == "ok" else "dialog-warning-symbolic")
        self.safety_icon.set_css_classes(["vifte-status-icon", {"ok": "calm", "emergency": "emergency"}.get(state, "warm")])
        safety = st.get("safety") or {}
        mcp = bool(safety.get("mcp_enabled"))   # mens Claude har tilgang er begge på og låst, som i Innstillinger
        self._ov_syncing = True
        self.ov_failsafe.set_active(mcp or bool(safety.get("failsafe_enabled")))
        self.ov_lost.set_active(mcp or bool(safety.get("sensor_lost_enabled", True)))
        self._ov_syncing = False
        for sw in (self.ov_failsafe, self.ov_lost):
            sw.set_sensitive(not mcp)
            sw.set_tooltip_text(_("Begge er alltid på mens Claude-tilgang er slått på.") if mcp else None)
        self.ov_failsafe_sub.set_label(_("Alle styrte vifter på 100 % ved {}").format(
            m.fmt_temp(safety.get("failsafe_temp", limit))))
        self.ov_lost_sub.set_label(_("{} % hvis en kurves sensor forsvinner").format(safety.get("sensor_lost_speed", 80)))
        paused = bool(st.get("paused"))
        self.ov_control_sub.set_label(_("BIOS styrer viftene") if paused else _("Vifte styrer viftene"))
        self.ov_pause_icon.set_from_icon_name("media-playback-start-symbolic" if paused else "media-playback-pause-symbolic")
        self.ov_pause_label.set_label(_("Fortsett") if paused else _("Pause"))

    def on_close(self, *_a):
        app = self.get_application()
        if app.tray and app.tray.available:
            self.set_visible(False)  # lukk til statusfeltet, som i FanControl
            return True
        return False

    def open_dialog(self, cls, *args):
        """Åpner en dialog. Feiler den (f.eks. uventede data i en importert fil), vises en
        melding i stedet for at knappen bare ikke gjør noe."""
        try:
            dlg = cls(self, *args)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            self.toast(_("Kunne ikke åpne: {}").format(ex))
            return None
        dlg.present(self)
        return dlg

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
            elif m.state.get("configs") != getattr(self, "_configs_shown", None):
                self.build_profiles()   # f.eks. etter sletting eller når en ulagret ny er forkastet
            if self.get_visible() and time.monotonic() - self._history_at >= 5:
                self._history_at = time.monotonic()
                m.history = m.client.call("history")
        except Exception as ex:
            if self.connected or self._structure_key is None:
                debuglog.error("lost contact with vifted (or failed to show its state): %s", ex, exc=True)
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
            debuglog.error("could not send a change: %s", ex, exc=True)
            self.toast(_("Kunne ikke sende endring: {}").format(_(str(ex))))
            self.model.rev = -1   # hent tjenestens utgave på nytt ved neste oppdatering
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
        self.narrow_hide = []
        self.build_profiles()
        self.build_overview_fans()
        self.build_controls()
        self.build_curves()
        self.build_speeds()
        self.build_sensors()
        self.apply_narrow()
        for name, val in (("show-hidden", self.model.show_hidden),
                          ("pause", bool(self.model.state.get("paused")))):
            self.lookup_action(name).set_state(GLib.Variant.new_boolean(val))
        self.update_values()

    def build_profiles(self):
        m = self.model
        self._configs_shown = m.state.get("configs")
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
        self.profile_label.set_label(name + (" •" if dirty else ""))
        if dirty:
            self.save_btn.add_css_class("suggested-action")
        else:
            self.save_btn.remove_css_class("suggested-action")
        self.update_page_subtitle()

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
            lst = sec.new_list(hwname, group_kind([m.local_id(e.get("Identifier")) for e, _l in items]))
            slim = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                               column_spacing=8, row_spacing=8, visible=False)
            for e, local in items:
                lst.append(self.control_row(e, local, slim, lst))
            sec.box.append(slim)

    def control_row(self, e, local, slim, lst):
        """Rad på Kontroller: vifteikon, navn og turtall, kurvevelger, prosent, stolpe, bryter og tannhjul."""
        m, cfg = self.model, self.model.cfg
        ident = e.get("Identifier")
        title = e.get("NickName") or e.get("Name") or ident
        row = Gtk.ListBoxRow(activatable=False, css_classes=["vifte-row", "vifte-fan-row"])
        if e.get("IsHidden"):
            row.add_css_class("vifte-hidden")
        box = Gtk.Box(spacing=12)
        box.append(icon_surface("fan"))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=title, xalign=0, ellipsize=3, css_classes=["heading"]))
        line = Gtk.Box(spacing=6)
        rpm = Gtk.Label(xalign=0, ellipsize=3, label="" if local else ident,
                        css_classes=["caption", "dim-label", "vifte-sub"])
        manual = Gtk.Label(css_classes=["caption", "accent"])
        line.append(rpm)
        line.append(manual)
        texts.append(line)
        box.append(texts)
        # En utgang uten vifte vises som en liten merkelapp under gruppen i stedet for en hel rad.
        chip = Gtk.Button(label=f"{title} · {_('Ingen vifte (0 RPM)')}", tooltip_text=_("Innstillinger"))
        chip.add_css_class("flat")
        chip.add_css_class("vifte-slim")
        chip.connect("clicked", lambda *_a: self.open_dialog(ControlDialog, ident))
        slim.append(chip)
        chip.get_parent().set_visible(False)

        sw = Gtk.Switch(active=bool(e.get("Enable")), valign=Gtk.Align.CENTER, tooltip_text=_("Aktiver styring"))
        names = [_("(ingen kurve)")] + [c.get("Name") for c in cfg.curves]
        dd = Gtk.DropDown(model=Gtk.StringList.new(names), valign=Gtk.Align.CENTER)
        dd.set_size_request(184, -1)   # lik bredde i alle rader
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
        pct = Gtk.Label(label="–", xalign=1, width_chars=5, css_classes=["vifte-value"])
        level = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER)
        level.set_size_request(96, -1)
        self.narrow_hide.append(level)   # skjules i smale vinduer, så navnet får plass
        gear = Gtk.Button(icon_name="emblem-system-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Innstillinger"),
                          css_classes=["flat"])
        gear.connect("clicked", lambda *_a: self.open_dialog(ControlDialog, ident))
        for w in (dd, pct, level, sw, gear):
            box.append(w)
        row.set_child(box)

        def update():
            st = (m.state.get("control_state") or {}).get(ident, {})
            ent = cfg.control(ident) or {}
            out = st.get("out")
            read = st.get("read")
            no_fan = bool(st.get("no_fan"))
            row.set_visible(not no_fan)
            chip.get_parent().set_visible(no_fan)
            slim.set_visible(any(c.get_visible() for c in slim))
            lst.set_visible(any(r.get_visible() for r in lst))
            val = out if out is not None else read
            pct.set_label(f"{val:.0f} %" if val is not None else "–")
            level.set_value(val or 0)
            r = st.get("rpm")
            pump = st.get("pump")
            if local:
                rpm.set_label(f"{r:.0f} RPM" if r is not None else (_("BIOS styrer") if out is None else ""))
            if out is None:
                row.add_css_class("idle")   # styres ikke av Vifte: svakere tone
            else:
                row.remove_css_class("idle")
            if pump:
                manual.set_label(_("PUMPE"))
            else:
                manual.set_label(_("MANUELL") if ent.get("ManualControl") and ent.get("Enable") else "")
            # En pumpe styres fra maskininnstillingen (tannhjulet), ikke av kurven her.
            sw.set_sensitive(not pump)
            dd.set_sensitive(not pump)
            sw.set_tooltip_text(_("Pumpe – endres under tannhjulet") if pump else _("Aktiver styring"))
        self.updaters.append(update)
        return row

    # ---- kurver
    def build_curves(self):
        """Lister kurvene og viser den valgte. Valget og rulleposisjonen beholdes når siden bygges på nytt."""
        cfg, page = self.model.cfg, self.pages["curves"]
        adj = page.detail_scroll.get_vadjustment()
        before, pos = self.curve_selected, adj.get_value()
        page.clear()
        if not cfg.curves:
            page.detail.set_child(Adw.StatusPage(icon_name="power-profile-performance-symbolic",
                                                 title=_("Ingen kurver ennå"),
                                                 description=_("Lag en kurve og velg den på en kontroll.")))
            return
        rows = {}
        for c in cfg.curves:
            if self.visible(c):
                row = self.curve_row(c)
                page.list.append(row)
                rows[c.get("Name")] = row
        name = self.curve_selected if self.curve_selected in rows else next(iter(rows), None)
        if name:
            self._curve_building = True
            page.list.select_row(rows[name])
            self._curve_building = False
        self.show_curve(name)
        if name == before:
            GLib.idle_add(lambda: adj.set_value(pos) and False)

    def show_curve(self, name):
        page = self.pages["curves"]
        self.curve_selected = name
        for row in page.list:
            row.mini.active = row.name == name
            row.mini.queue_draw()
        if name is None:
            page.detail.set_child(None)
            return
        editor = CurveEditor(self, name)
        page.detail.set_child(editor)
        editor.update_live()

    def curve_row(self, c):
        """Rad i kurvelisten: navn, type og kilde, kurveformen og nåverdien."""
        m = self.model
        name = c.get("Name")
        kind = curve_type(c)
        src = (c.get("SelectedTempSource") or {}).get("Identifier")
        sub = _(CURVE_TITLES[kind])
        if src:
            sub += " · " + m.cfg.display_name(src, src)
        elif kind == "mix":
            sub += " · " + ", ".join(r.get("Name", "?") for r in c.get("SelectedFanCurves") or [])
        row = Gtk.ListBoxRow(css_classes=["vifte-curve-row"])
        row.name = name
        row.search_text = f"{name} {sub}".lower()
        if c.get("IsHidden"):
            row.add_css_class("vifte-hidden")
        box = Gtk.Box(spacing=10)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=name, xalign=0, ellipsize=3, css_classes=["vifte-row-title"]))
        sub_label = Gtk.Label(label=sub, xalign=0, ellipsize=3, css_classes=["caption", "dim-label"])
        texts.append(sub_label)
        box.append(texts)
        mini = MiniCurve()

        def num(key, default):
            return float(c.get(key, default) or default)
        if kind == "graph":
            mini.points = sorted(parse_point(p) for p in c.get("Points") or [])
            mini.x_range = (num("MinimumTemperature", 20), num("MaximumTemperature", 120))
            mini.y_range = (0, num("MaximumCommand", 100))
        elif kind == "linear":
            mini.points = [(num("MinimumTemperature", 40), num("MinimumFanSpeed", 20)),
                           (num("MaximumTemperature", 80), num("MaximumFanSpeed", 100))]
        elif kind == "flat":
            mini.points = [(20, num("Percent", 50)), (100, num("Percent", 50))]
        elif kind == "trigger":
            load = num("LoadTemperature", 70)
            mini.points = [(num("IdleTemperature", 50), num("IdleFanSpeed", 30)), (load, num("IdleFanSpeed", 30)),
                           (load + 0.01, num("LoadFanSpeed", 100))]
        if mini.x_range[1] <= mini.x_range[0]:
            mini.x_range = (20, 120)
        row.mini = mini
        box.append(mini)
        value = Gtk.Label(label="–", xalign=1, width_chars=6, css_classes=["vifte-value"])
        box.append(value)
        row.set_child(box)
        unit = " RPM" if int(c.get("CommandMode", 0) or 0) == 1 else " %"

        def update():
            st = (m.state.get("curves") or {}).get(name) or {}
            v, t = st.get("value"), st.get("input")
            value.set_label("–" if v is None else f"{v:.0f}{unit}")
            if src and t is None and m.temp_value(src) is None:
                sub_label.set_label(_("Sensor mangler"))
                sub_label.add_css_class("vifte-missing")
        self.updaters.append(update)
        return row

    def accent_text(self):
        """Aksentfargen for tekst (til Pango-markup): Vifte-oransje eller skrivebordets aksent."""
        ok, c = self.get_style_context().lookup_color("vifte_accent_text")
        if ok:
            return "#{:02X}{:02X}{:02X}".format(*(round(x * 255) for x in (c.red, c.green, c.blue)))
        return PALETTE["dark" if Adw.StyleManager.get_default().get_dark() else "light"]["vifte_accent_text"]

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
        self.curve_selected = name   # den nye kurven velges i listen
        self.config_changed(True)

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
            kind = group_kind([m.local_id(e.get("Identifier")) for e in items])
            lst = sec.new_list(hwname, kind, movable=True)
            self.make_group_movable(sec.header, "speeds", hwname, cfg.fan_sensors)
            for e in items:
                lst.append(self.sensor_row(e, "fan", GROUP_STYLE[kind][3], cfg.fan_sensors, hwname, "speeds"))

    def sensor_row(self, e, kind, color, lst, group, page):
        """Rad for en maskinvaresensor; klikk åpner sensormenyen. Kan dras innen gruppen."""
        ident = e.get("Identifier")
        name = self.model.display_name(ident)
        own = e.get("NickName") or e.get("Name") or ident
        row = self.value_row(ident, name, own if name != own else None, kind, color,
                             lambda: self.sensor_menu(ident), e.get("IsHidden"))
        self.make_movable(row, ident, lst, group, page)
        return row

    def value_row(self, ident, title, subtitle, kind, color, on_open, hidden=False):
        """Rad på Hastigheter/Sensorer: navn (og status når varm/het), sparkline, verdi og pil.
        kind: «fan» (RPM) eller «temperature»."""
        m = self.model
        row = Gtk.ListBoxRow(css_classes=["vifte-row"])
        row.on_open = on_open
        if hidden:
            row.add_css_class("vifte-hidden")
        box = Gtk.Box(spacing=12)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Label(label=title, xalign=0, ellipsize=3, css_classes=["vifte-row-title"]))
        chip = StatusChip()
        line.append(chip)
        texts.append(line)
        if subtitle:
            texts.append(Gtk.Label(label=subtitle, xalign=0, ellipsize=3, css_classes=["caption", "dim-label"]))
        box.append(texts)
        spark = Sparkline(min_span=300 if kind == "fan" else 5, height=28, color=color, dot=True)
        spark.set_size_request(150, -1)
        spark.set_hexpand(False)
        spark.set_valign(Gtk.Align.CENTER)
        value = Gtk.Label(label="–", xalign=1, width_chars=9, css_classes=["vifte-value"])
        arrow = Gtk.Button(icon_name="pan-end-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Detaljer"),
                           css_classes=["flat"])
        arrow.connect("clicked", lambda *_a: on_open())
        for w in (spark, value, arrow):
            box.append(w)
        row.set_child(box)
        names = {"warm": _("Varm"), "hot": _("Het")}

        def fmt(v):
            return f"{v:.0f} RPM" if kind == "fan" else m.fmt_temp(v)

        def update():
            v = m.temp_value(ident)
            value.set_label("–" if v is None else fmt(v))
            status = temp_status(v) if kind != "fan" else None
            chip.set_status(status if status in names else None, names.get(status, ""))  # bare når varm/het
            t, vals = m.history_values(ident)
            spark.set_values(vals)
            known = [x for x in vals if x is not None]
            minutes = round((t[-1] - t[0]) / 60) if len(t) > 1 else 0
            spark.set_tooltip_text(_("Siste {} min: {} – {}").format(minutes, fmt(min(known)), fmt(max(known)))
                                   if known and minutes >= 1 else None)
        self.updaters.append(update)
        return row

    def make_movable(self, card, ident, lst, group, page=None):
        """Kortet kan dras og slippes på et annet kort i samme gruppe. Rekkefølgen er rekkefølgen
        i listen i konfigurasjonen (som i FanControl), så den følger med filen. En gruppe som dras
        hit (fra overskriften), flyttes til denne gruppens plass."""
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda _s, _x, _y: Gdk.ContentProvider.new_for_value(f"{group}\n{ident}"))
        src.connect("drag-begin", lambda s, _d: s.set_icon(Gtk.WidgetPaintable.new(card),
                                                           card.get_width() // 2, 20))
        card.add_controller(src)
        self._drop_target(card, lambda value: self._handle_drop(value, lst, page, group, ident))

    def make_group_movable(self, header, page, group, lst):
        """Hele gruppen dras i overskriften og slippes på en annen gruppe på samme side."""
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda _s, _x, _y: Gdk.ContentProvider.new_for_value(f"GROUP\n{page}\n{group}"))
        src.connect("drag-begin", lambda s, _d: s.set_icon(Gtk.WidgetPaintable.new(header), 20, 10))
        header.add_controller(src)
        self._drop_target(header, lambda value: self._handle_drop(value, lst, page, group, None))

    def _drop_target(self, widget, handler):
        dst = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        dst.connect("enter", lambda *_a: (widget.add_css_class("vifte-drop"), Gdk.DragAction.MOVE)[1])
        dst.connect("leave", lambda *_a: widget.remove_css_class("vifte-drop"))

        def drop(_t, value, _x, _y):
            widget.remove_css_class("vifte-drop")
            return handler(value or "")
        dst.connect("drop", drop)
        widget.add_controller(dst)

    def _handle_drop(self, value, lst, page, group, ident):
        if value.startswith("GROUP\n"):
            _, src_page, src_group = value.split("\n", 2)
            if page is None or src_page != page or src_group == group:
                return False
            GLib.idle_add(lambda: self.move_group(lst, src_group, group) and False)
            return True
        g, _, other = value.partition("\n")
        if ident is None or g != group or other == ident:
            return False
        GLib.idle_add(lambda: self.move_card(lst, other, ident) and False)
        return True

    def group_of(self, entry):
        s = self.model.sensor(entry.get("Identifier"))
        return s["hardware"] if s else _("Mangler på denne maskinen")

    def move_group(self, lst, src_group, dst_group):
        if move_group(lst, self.group_of, src_group, dst_group):
            self.config_changed(True)

    def move_card(self, lst, src_id, dst_id):
        if move_entry(lst, src_id, dst_id):
            self.config_changed(True)

    def sensor_menu(self, ident):
        entry = self.model.cfg.sensor_entry(ident)
        dlg = Adw.AlertDialog(heading=self.model.display_name(ident), body=ident)
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
                RenameDialog.ask(self, _("Nytt navn"), self.model.display_name(ident),
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
        if cfg.custom_sensors:
            lst = sec.new_list(_("Egendefinerte sensorer"), "custom")
            for s in cfg.custom_sensors:
                ident = s.get("Identifier")
                row = self.value_row(ident, s.get("Name") or ident, _(SENSOR_TYPES[custom_sensor_type(s)]),
                                     "temperature", GROUP_STYLE["custom"][2],
                                     lambda i=ident: self.open_dialog(CustomSensorDialog, i))
                self.make_movable(row, ident, cfg.custom_sensors, "custom", None)
                lst.append(row)
        groups = {}
        for e in cfg.temp_sensors:
            if self.visible(e):
                s = m.sensor(e.get("Identifier"))
                groups.setdefault(s["hardware"] if s else _("Mangler på denne maskinen"), []).append(e)
        for hwname, items in sorted(groups.items(), key=lambda g: g[0] == _("Mangler på denne maskinen")):
            kind = group_kind([m.local_id(e.get("Identifier")) for e in items])
            lst = sec.new_list(hwname, kind, movable=True)
            self.make_group_movable(sec.header, "sensors", hwname, cfg.temp_sensors)
            for e in items:
                lst.append(self.sensor_row(e, "temperature", GROUP_STYLE[kind][2], cfg.temp_sensors, hwname,
                                           "sensors"))

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
        self.open_dialog(CustomSensorDialog, s["Identifier"])

    # ================================================================ løpende oppdatering
    def update_values(self):
        st = self.model.state
        self.update_tint()
        self.update_overview()
        self.update_sidebar_status()
        for u in self.updaters:
            try:
                u()
            except Exception as ex:  # en defekt oppdatering skal ikke stoppe resten
                print("oppdatering:", ex, file=sys.stderr)
                debuglog.error_once(("update", repr(ex)), "update of a row failed: %s", ex)
        for d in list(self.live_dialogs):
            try:
                d.update_live()
            except Exception as ex:  # en defekt dialog skal ikke stoppe resten
                print("dialog:", ex, file=sys.stderr)
                debuglog.error_once(("dialog", repr(ex)), "update of a dialog failed: %s", ex)
        self.update_header()
        self.update_tray()
        self.show_mcp_activity(st.get("mcp_log") or [])
        self.check_alerts()
        # bannere – nødmodus har høyest prioritet
        fs = st.get("failsafe") or {}
        if fs.get("active"):
            trig = fs.get("trigger") or [None, None]
            self.banner.set_title(_("NØDMODUS: {} er {} – alle styrte vifter går på 100 %").format(
                self.model.display_name(trig[0], trig[0]), self.model.fmt_temp(trig[1])))
            self.banner.set_button_label("")
            self.banner.add_css_class("error")
            self.banner.set_revealed(True)
            self.progress.set_visible(False)
            return
        lost = (st.get("sensor_lost") or {}).get("controls") or {}
        if lost:
            sensors = sorted({i for ids in lost.values() for i in ids})
            self.banner.set_title(_("Sensor svarer ikke: {} – {} vifte(r) går på {} %").format(
                ", ".join(self.model.display_name(i, i) for i in sensors), len(lost),
                round((st.get("sensor_lost") or {}).get("speed", 80))))
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

    def check_alerts(self):
        """Skrivebordsvarsler (frivillig, slås på under Innstillinger → Sikkerhet → Varsler)."""
        from ..i18n import get_setting
        m, st = self.model, self.model.state
        if self.get_application().window is not self:
            return
        enabled = bool(get_setting("alerts_enabled", False))
        rules = (get_setting("alerts", []) or []) if enabled else []
        events = self.alerts.check(time.monotonic(), rules, m.temp_value,
                                   (st.get("failsafe") or {}).get("active"),
                                   ((st.get("sensor_lost") or {}).get("controls") or {}).keys())
        if not enabled:
            return
        for ev in events:
            if ev[0] == "temp":
                rule, v = ev[1], ev[2]
                name = m.display_name(rule["sensor"], rule["sensor"])
                title = _("{} er {}").format(name, m.fmt_temp(v))
                body = _("Over varslingsgrensen på {}").format(m.fmt_temp(float(rule["temp"])))
                nid = "vifte-alert-" + AlertMonitor.key(rule)
            elif ev[0] == "failsafe":
                trig = (st.get("failsafe") or {}).get("trigger") or [None, None]
                title = _("Nødmodus: alle styrte vifter går på 100 %")
                body = _("{} er {}").format(m.display_name(trig[0], trig[0]), m.fmt_temp(trig[1]))
                nid = "vifte-failsafe"
            else:
                lost = (st.get("sensor_lost") or {}).get("controls") or {}
                sensors = sorted({i for c in ev[1] for i in lost.get(c, [])})
                title = _("Sensor svarer ikke")
                body = _("{} – {} vifte(r) går på {} %").format(
                    ", ".join(m.display_name(i, i) for i in sensors), len(ev[1]),
                    round((st.get("sensor_lost") or {}).get("speed", 80)))
                nid = "vifte-sensor-lost"
            n = Gio.Notification.new(title)
            n.set_body(body)
            n.set_priority(Gio.NotificationPriority.HIGH if ev[0] != "temp" else Gio.NotificationPriority.NORMAL)
            n.set_default_action("app.show-window")
            self.get_application().send_notification(nid, n)

    def update_tray(self):
        tray = self.get_application().tray
        if not tray or self.get_application().window is not self:
            return
        m = self.model
        st = m.state
        temps = self.tray_temperatures()
        key = (tuple(st.get("configs", [])), m.name, bool(st.get("paused")), i18n.current(), self.connected,
               tuple(temps))
        if key != getattr(self, "_tray_key", None):
            self._tray_key = key
            configs = [{"label": n[:-5] if n.endswith(".json") else n, "toggle": "radio",
                        "state": n == m.name, "action": (lambda n=n: self.switch_config(n))}
                       for n in st.get("configs", [])]
            # Temperaturene øverst, adskilt fra resten med en skillestrek
            top = [{"key": k, "label": label, "action": self.get_application().show_window}
                   for k, label in temps.items()]
            if top:
                top.append({"separator": True})
            tray.set_menu(top + [
                {"label": _("Vis Vifte"), "action": self.get_application().show_window},
                {"separator": True},
                {"label": _("Konfigurasjoner"), "children": configs, "enabled": bool(configs)},
                {"label": _("Pause styring (BIOS overtar)"), "toggle": "checkmark",
                 "state": bool(st.get("paused")), "enabled": self.connected,
                 "action": lambda: self.call("settings", paused=not self.model.state.get("paused"))},
                {"separator": True},
                {"label": _("Avslutt"), "action": self.request_quit},
            ])
        tray.menu.update_labels(temps)
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
            name = m.display_name(ident, ident)
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

    def auto_tray_sensor(self, kind):
        """Standardsensor for menyen: CPU-kjernetemperatur eller GPU-temperatur."""
        sensors = [s for s in self.model.state.get("sensors", []) if s["kind"] == "temperature"]
        if kind == "cpu":
            prefs = [lambda s: s["id"] == "/amdcpu/0/temperature/2",
                     lambda s: s["id"].startswith("/intelcpu/") and "package" in s["name"].lower(),
                     lambda s: s["id"].startswith(("/amdcpu/", "/intelcpu/"))]
        else:
            prefs = [lambda s: s["id"].startswith("NVApiWrapper/") and s["id"].endswith("/sensor/0"),
                     lambda s: s["id"].startswith("/gpu-amd/") and s["name"].lower() == "edge",
                     lambda s: s["id"].startswith(("NVApiWrapper/", "/gpu-amd/"))]
        for pref in prefs:
            hit = next((s for s in sensors if pref(s)), None)
            if hit:
                return hit["id"]
        return None

    def tray_temperatures(self):
        """{"cpu": "CPU: 45 °C", "gpu": "GPU: 38 °C"} etter brukerens valg i innstillingene."""
        from ..i18n import get_setting
        m = self.model
        out = {}
        for kind in ("cpu", "gpu"):
            choice = get_setting(f"tray_{kind}_sensor", "auto")
            if choice == "hide":
                continue
            ident = self.auto_tray_sensor(kind) if choice == "auto" else choice
            if not ident:
                continue
            v = m.temp_value(ident)
            if v is None:
                text = "–"
            elif m.fahrenheit:
                text = f"{v * 9 / 5 + 32:.0f} °F"
            else:
                text = f"{v:.0f} °C"
            out[kind] = f"{kind.upper()}: {text}"
        return out

    def banner_clicked(self, _b):
        act = getattr(self, "banner_action", None)
        if act == "cancel":
            self.call("cancel")
        elif act == "remap":
            self.open_dialog(RemapDialog)
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

        def start():
            fd.open(self, None, done)
        # Importen bytter til den nye konfigurasjonen; spør før ulagrede endringer forsvinner.
        if self.model.state.get("dirty"):
            self.confirm_discard(start)
        else:
            start()

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
            if self.call("new", name=name) is not None:
                self.poll()

        def ask():
            RenameDialog.ask(self, _("Navn på ny konfigurasjon"), _("Ny"), make)
        if self.model.state.get("dirty"):
            self.confirm_discard(ask)
        else:
            ask()

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

    def refresh_stress_menu(self):
        """«Åpne Stress» hvis programmet er installert, ellers «Last ned Stress …»."""
        self.stress_menu.remove_all()
        self.stress_menu.append(_("Åpne Stress") if stress_path() else _("Last ned Stress …"), "win.stress")

    def open_stress(self):
        path = stress_path()
        if path:
            try:
                Gio.Subprocess.new([path], Gio.SubprocessFlags.NONE)
            except GLib.Error as ex:
                self.toast(_("Kunne ikke starte Stress: {}").format(ex.message))
            return
        dlg = Adw.AlertDialog(
            heading=_("Last ned Stress"),
            body=_("Stress er et eget, frivillig program fra samme utvikler. Det setter prosessoren og "
                   "skjermkortet under kontrollert last mens du følger temperaturene, som er nyttig når "
                   "du justerer viftekurver. Vifte virker like godt uten.\n\nInstallasjonen er beskrevet "
                   "på GitHub-siden."))
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("open", _("Åpne GitHub"))
        dlg.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "open" and Gtk.UriLauncher.new(STRESS_URL).launch(self, None, None))
        dlg.present(self)

    def set_names_follow_pairing(self, on):
        from ..i18n import set_setting
        set_setting("names_follow_pairing", on)
        self.model.names_follow_pairing = on
        self.rebuild()
        self._tray_key = None
        self.update_tray()

    def set_fahrenheit(self, on):
        self.model.cfg.window["Fahrenheit"] = on
        self.config_changed(True)

    def toggle_pause(self, act):
        v = not act.get_state().get_boolean()
        act.set_state(GLib.Variant.new_boolean(v))
        self.call("settings", paused=v)

    def settings(self, tab=None):
        """tab: «safety» åpner fanen Sikkerhet (fra statuskortet i sidefeltet)."""
        dlg = Adw.PreferencesDialog(title=_("Innstillinger"))
        # Faner: Generelt, Sikkerhet (nødmodus, sensortap, varsler) og Claude
        page = Adw.PreferencesPage(title=_("Generelt"), icon_name="preferences-system-symbolic")
        dlg.add(page)
        safety_page = Adw.PreferencesPage(title=_("Sikkerhet"), icon_name="security-high-symbolic")
        dlg.add(safety_page)
        claude_page = Adw.PreferencesPage(title="Claude", icon_name="user-available-symbolic")
        dlg.add(claude_page)
        g = Adw.PreferencesGroup(title="Språk / Language")
        page.add(g)
        langs = [(k, _(label)) for k, label in LANGUAGES]
        g.add(combo_row("Språk / Language", langs, language_setting(),
                        lambda v: self.change_language(v, dlg)))
        if self.get_application().tray:
            from ..i18n import get_setting, set_setting
            g = Adw.PreferencesGroup(title=_("Statusfelt"))
            page.add(g)
            if os.path.exists(AUTOSTART_SYSTEM):
                g.add(switch_row(_("Start i statusfeltet ved innlogging"), autostart_enabled(), set_autostart,
                                 _("Viser ikonet i statusfeltet uten å åpne vinduet")))
            sensors = [("auto", _("Automatisk")), ("hide", _("Skjul"))] + self.model.temp_choices()
            for kind, title in (("cpu", _("CPU-temperatur i menyen")), ("gpu", _("GPU-temperatur i menyen"))):
                g.add(combo_row(title, sensors, get_setting(f"tray_{kind}_sensor", "auto"),
                                lambda v, k=kind: (set_setting(f"tray_{k}_sensor", v), self.update_tray()),
                                subtitle=_("Vises øverst i menyen til ikonet i statusfeltet")))
        g = Adw.PreferencesGroup(title=_("Visning"))
        page.add(g)
        w = self.model.cfg.window
        g.add(combo_row(_("Tema"), [("system", _("Følg systemet")), ("light", _("Lyst")), ("dark", _("Mørkt"))],
                        self.theme_choice(), self.set_theme))
        from ..i18n import get_setting as setting
        g.add(switch_row(_("Farge følger temperaturen"), setting("tint_enabled", False), self.set_tint,
                         _("Svak fargetone i vinduet: kjølig ved tomgang, varm under last, rød i nødmodus")))
        accent = switch_row(_("Bruk skrivebordets aksentfarge"),
                            accent_supported() and setting("system_accent", True), self.set_system_accent,
                            _("Av: Vifte bruker sin egen oransje") if accent_supported()
                            else _("Krever GNOME 47 (libadwaita 1.6) eller nyere"))
        accent.set_sensitive(accent_supported())
        g.add(accent)
        g.add(switch_row(_("Fahrenheit"), self.model.fahrenheit, self.set_fahrenheit,
                         _("Vis temperaturer i °F i stedet for °C")))
        g.add(switch_row(_("Hastigheter bruker viftenavnet"), self.model.names_follow_pairing,
                         self.set_names_follow_pairing,
                         _("En hastighetsmåler uten eget navn vises med navnet til viften den er paret med")))
        g.add(switch_row(_("Skjul hastighetskort"), w.get("HideFanSpeedCards"),
                         lambda v: (w.__setitem__("HideFanSpeedCards", v), self.apply_view())))
        self.build_update_settings(page)
        g = Adw.PreferencesGroup(title=_("Tjeneste"))
        page.add(g)
        g.add(spin_row(_("Oppdateringsintervall (s)"), self.model.state.get("interval", 1.0), 0.25, 10, 0.25,
                       lambda v: self.call("settings", interval=v), digits=2))
        self.build_debug_settings(page, dlg)
        self.build_safety_settings(safety_page)
        self.build_alert_settings(safety_page)
        self.build_mcp_settings(claude_page)
        # Avinstallering ligger nederst her (ikke i hovedmenyen), så det må være et aktivt valg.
        if os.path.exists(UNINSTALLER):
            g = Adw.PreferencesGroup()
            page.add(g)
            row = Adw.ActionRow(title=_("Avinstaller Vifte"),
                                subtitle=_("Tjenesten stoppes og viftene gis tilbake til BIOS. Programmet, "
                                           "menyoppføringen og driveroppsettet fjernes."))
            btn = Gtk.Button(label=_("Avinstaller"), valign=Gtk.Align.CENTER)
            btn.add_css_class("destructive-action")
            btn.connect("clicked", lambda *_a: (dlg.close(), self.uninstall()))
            row.add_suffix(btn)
            g.add(row)
        if tab == "safety":
            dlg.set_visible_page(safety_page)
        dlg.present(self)

    def build_mcp_settings(self, page):
        safety = self.model.state.get("safety") or {}
        top = Adw.PreferencesGroup()
        page.add(top)
        self.claude_hero = StatusHero()
        top.add(self.claude_hero)
        g = Adw.PreferencesGroup(
            title=_("Claude (MCP)"),
            description=_("La Claude lese sensorer og justere kurver og vifter for deg. Sikkerhetsgrensene "
                          "under håndheves av Vifte og kan ikke overstyres av Claude."))
        page.add(g)
        rows = []
        enable = switch_row(_("Tillat styring fra Claude"), safety.get("mcp_enabled"),
                            lambda v: self.set_mcp_enabled(v, rows[0]),
                            _("Nødmodus og sikring ved sensortap er alltid på mens dette er slått på"))
        rows.append(enable)
        g.add(enable)
        g.add(spin_row(_("Laveste viftehastighet (%)"), safety.get("floor", 25), 10, 60, 1,
                       lambda v: self.call("settings", mcp_floor=v),
                       subtitle=_("Claude kan aldri sette en vifte lavere enn dette, og aldri stoppe den")))
        row = Adw.ActionRow(title=_("Koble til Claude Code"), subtitle="claude mcp add --scope user vifte -- vifte mcp")
        row.set_subtitle_selectable(True)
        if shutil.which("claude"):
            btn = Gtk.Button(label=_("Legg til"), valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_a: self.register_claude_code())
            row.add_suffix(btn)
        g.add(row)
        row = Adw.ActionRow(title=_("Promptforslag"),
                            subtitle=_("Ferdig tekst du kan lime inn i Claude for å sette opp og teste viftene"))
        btn = Gtk.Button(label=_("Vis"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_a: PromptDialog().present(self))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        g.add(row)
        self.sync_claude_hero(safety)

    def sync_claude_hero(self, safety):
        if not safety.get("mcp_enabled"):
            self.claude_hero.set_status(_("Claude har ikke tilgang"), _("Slå på bryteren under for å gi tilgang"), "warm")
            return
        pumps = safety.get("pumps") or {}
        names = [c.get("NickName") or c.get("Name") for c in self.model.cfg.controls
                 if self.model.local_id(c.get("Identifier")) in pumps]
        text = _("Laveste hastighet {} %").format(safety.get("floor", 25))
        if names:
            text += " · " + _("{} er beskyttet").format(", ".join(names))
        self.claude_hero.set_status(_("Claude har tilgang"), text)

    def build_safety_settings(self, page):
        safety = self.model.state.get("safety") or {}
        top = Adw.PreferencesGroup()
        page.add(top)
        hero = StatusHero()
        top.add(hero)
        g = Adw.PreferencesGroup(title=_("Viftevern"))
        page.add(g)
        syncing = [False]   # ikke send endringer når bryterne oppdateres herfra

        def setter(key):
            return lambda v: syncing[0] or self.call("settings", **{key: v})
        failsafe = switch_row(_("Nødmodus"), False, setter("failsafe_enabled"),
                              _("Når CPU eller GPU når nødtemperaturen, går alle styrte vifter på 100 %"))
        g.add(failsafe)
        g.add(spin_row(_("Nødtemperatur (°C)"), safety.get("failsafe_temp", 95), 70, 105, 1,
                       lambda v: self.call("settings", failsafe_temp=v)))
        lost = switch_row(_("Sikring ved sensortap"), False, setter("sensor_lost_enabled"),
                          _("Hvis sensoren til en kurve slutter å svare, går viftene på hastigheten under "
                            "til den er tilbake"))
        g.add(lost)
        g.add(spin_row(_("Hastighet ved sensortap (%)"), safety.get("sensor_lost_speed", 80), 30, 100, 5,
                       lambda v: self.call("settings", sensor_lost_speed=v)))

        def sync(safety):
            """Mens Claude har tilgang er begge på og kan ikke slås av (bryterne vises på og
            grået ut); ellers viser de brukerens eget valg."""
            mcp = bool(safety.get("mcp_enabled"))
            syncing[0] = True
            failsafe.set_active(mcp or bool(safety.get("failsafe_enabled")))
            lost.set_active(mcp or bool(safety.get("sensor_lost_enabled", True)))
            syncing[0] = False
            failsafe.set_sensitive(not mcp)
            lost.set_sensitive(not mcp)
            emergency = mcp or safety.get("failsafe_enabled")
            sensor = mcp or safety.get("sensor_lost_enabled", True)
            hero.set_status(_("Beskyttelsen er aktiv") if emergency and sensor else _("Beskyttelsen er delvis av"),
                            " · ".join(([_("Nødmodus ved {}").format(self.model.fmt_temp(safety.get("failsafe_temp", 95)))]
                                        if emergency else [_("Nødmodus av")]) +
                                       ([_("{} % ved sensortap").format(safety.get("sensor_lost_speed", 80))]
                                        if sensor else [_("Sikring ved sensortap er av")])),
                            None if emergency and sensor else "warm")
            g.set_description(_("Claude har tilgang, så begge er slått på og kan ikke slås av. "
                                "Temperaturen og hastigheten kan fortsatt justeres.") if mcp else
                              _("Begge er alltid på mens Claude-tilgang er slått på."))
        sync(safety)
        self._sync_safety = sync

    def build_alert_settings(self, page):
        from ..i18n import get_setting, set_setting
        safety = self.model.state.get("safety") or {}
        top = int(safety.get("failsafe_temp", 95)) - 1
        g = Adw.PreferencesGroup(
            title=_("Varsler"),
            description=_("Skrivebordsvarsel når en sensor har vært over grensen i 10 sekunder, og når "
                          "nødmodus slår inn eller en sensor slutter å svare. Krever at Vifte kjører, "
                          "for eksempel i statusfeltet."))
        page.add(g)
        rows = []

        def rules():
            return [dict(r) for r in get_setting("alerts", []) or []]

        def rebuild():
            for r in rows:
                g.remove(r)
            rows.clear()
            choices = self.model.temp_choices()
            for n, rule in enumerate(rules()):
                row = combo_row(_("Varsel {}").format(n + 1), choices, rule.get("sensor"),
                                lambda v, n=n: edit(n, sensor=v))
                adj = Gtk.Adjustment(value=min(float(rule.get("temp", 85)), top), lower=40, upper=top,
                                     step_increment=1, page_increment=5)
                spin = Gtk.SpinButton(adjustment=adj, valign=Gtk.Align.CENTER)
                spin.connect("value-changed", lambda sb, n=n: edit(n, temp=int(sb.get_value())))
                row.add_suffix(spin)
                row.add_suffix(Gtk.Label(label="°C"))
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Fjern varsel"))
                rm.add_css_class("flat")
                rm.connect("clicked", lambda *_a, n=n: remove(n))
                row.add_suffix(rm)
                g.add(row)
                rows.append(row)
            add = Adw.ActionRow(title=_("Legg til varsel"))
            btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text=_("Legg til varsel"))
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_a: append())
            add.add_suffix(btn)
            add.set_activatable_widget(btn)
            g.add(add)
            rows.append(add)

        def edit(n, **kw):
            r = rules()
            r[n].update(kw)
            set_setting("alerts", r)

        def remove(n):
            r = rules()
            del r[n]
            set_setting("alerts", r)
            GLib.idle_add(lambda: rebuild() and False)

        def append():
            choices = [c[0] for c in self.model.temp_choices()]
            cpu = self.auto_tray_sensor("cpu")
            sensor = next((c for c in choices if self.model.local_id(c) == cpu), choices[0] if choices else None)
            set_setting("alerts", rules() + [{"sensor": sensor, "temp": min(85, top)}])
            rebuild()

        g.add(switch_row(_("Skrivebordsvarsler"), get_setting("alerts_enabled", False),
                         lambda v: set_setting("alerts_enabled", v)))
        rebuild()

    def build_update_settings(self, page):
        from ..i18n import set_setting
        g = Adw.PreferencesGroup(title=_("Oppdateringer"))
        page.add(g)
        g.add(switch_row(_("Se etter oppdateringer automatisk"), auto_enabled(),
                         lambda v: set_setting("auto_update_check", v),
                         _("Én gang i måneden, fra GitHub")))
        ts = last_checked()
        when = GLib.DateTime.new_from_unix_local(int(ts)).format("%x") if ts else _("aldri")
        row = Adw.ActionRow(title=_("Se etter oppdateringer nå"),
                            subtitle=_("Versjon {} · sist sjekket: {}").format(__version__, when))
        btn = Gtk.Button(label=_("Sjekk"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_a: self.get_application().updates.check(manual=True))
        row.add_suffix(btn)
        g.add(row)

    def set_mcp_enabled(self, on, row=None):
        if getattr(self, "_mcp_reverting", False):
            return
        if on:
            self.confirm_mcp(row)   # spør først; tilgangen slås på først når brukeren bekrefter
            return
        self._apply_mcp(False)

    def _apply_mcp(self, on):
        r = self.call("settings", mcp_enabled=on)
        if r is None:
            return False
        if getattr(self, "_sync_safety", None):
            self._sync_safety(r.get("safety") or {})  # Sikkerhet-fanen i samme dialog
        self.sync_claude_hero(r.get("safety") or {})
        # Ingen omstart trengs: tjenesten slipper Claude inn (eller ut) med en gang.
        self.toast(_("Claude-tilgang er slått på. Endringer lagres først når du ber om det.") if on
                   else _("Claude-tilgang er slått av"))
        return True

    def confirm_mcp(self, row):
        """Advarsel før Claude får tilgang: KI kan ta feil, og pumper bør merkes først."""
        pumps = (self.model.state.get("safety") or {}).get("pumps") or {}
        names = [self.model.cfg.display_name(c.get("Identifier")) for c in self.model.cfg.controls
                 if self.model.local_id(c.get("Identifier")) in pumps]
        body = (_("Claude er en KI og kan ta feil. Følg med på temperaturer og viftehastigheter etter hver "
                  "endring Claude gjør, og kontroller alltid resultatet selv.") + "\n\n" +
                _("Vifte håndhever sikkerhetsgrensene uansett: styrte vifter går aldri under laveste hastighet, "
                  "nødmodus og sikring ved sensortap er alltid på, og endringer lagres først når du ber om "
                  "det.") + "\n\n")
        if names:
            body += _("Merket som pumpe: {}. Claude kan ikke endre den.").format(", ".join(names))
        else:
            body += _("Har du AIO eller annen vannkjøling, bør du merke pumpen før du slår på Claude: "
                      "tannhjulet på pumpens kort → Type → Pumpe. Da kan verken Claude eller automatiske "
                      "funksjoner endre den, og du unngår at pumpen settes for lavt.")
        dlg = Adw.AlertDialog(heading=_("Slå på Claude-tilgang?"), body=body)
        dlg.add_response("cancel", _("Avbryt"))
        if not names:
            dlg.add_response("pump", _("Merk pumpen først"))
        dlg.add_response("enable", _("Slå på"))
        dlg.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def revert():
            if row is not None:
                self._mcp_reverting = True
                row.set_active(False)
                self._mcp_reverting = False

        def resp(_d, r):
            if r == "enable":
                if not self._apply_mcp(True):
                    revert()
                return
            revert()
            if r == "pump":
                prefs = row.get_ancestor(Adw.PreferencesDialog) if row is not None else None
                if prefs is not None:
                    prefs.close()
                self.show_page("controls")
                if not self.model.show_hidden:
                    self.activate_action("win.show-hidden", None)   # pumper er ofte skjult
                self.toast(_("Trykk på tannhjulet på pumpens kort og velg Type → Pumpe"))
        dlg.connect("response", resp)
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
        self.nav_rows["speeds"].set_visible(not hide)
        self.config_changed(False)

    def about(self):
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
        prov.load_from_path(STYLE_CSS)
        Gtk.StyleContext.add_provider_for_display(display, prov, 800)
        # fargetokens og aksent byttes når systemet går mellom lys og mørk modus
        self.palette = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self.palette, 800)
        sm = Adw.StyleManager.get_default()
        sm.connect("notify::dark", lambda *_a: self.apply_palette())
        if accent_supported():
            # CSS-en følger aksentfargen selv; grafene tegnes på nytt
            sm.connect("notify::accent-color", lambda *_a: self.apply_palette())
        self.apply_palette()
        # ikonene følger med pakken, så de finnes også når programmet kjøres fra kildekoden
        Gtk.IconTheme.get_for_display(display).add_search_path(ICON_DIR)
        Gtk.Window.set_default_icon_name(APP_ID)
        show = Gio.SimpleAction.new("show-window", None)
        show.connect("activate", lambda *_a: self.show_window())
        self.add_action(show)
        self.updates = UpdateManager(self)
        self.updates.start_auto()
        try:
            self.tray = Tray(self.show_window, "no.vifte.Vifte-symbolic", ICON_DIR)
            self.hold()  # fortsett å kjøre i statusfeltet når vinduet lukkes
        except Exception as ex:
            print("statusfelt utilgjengelig:", ex, file=sys.stderr)
            debuglog.error("system tray unavailable: %s", ex)
            self.tray = None

    def apply_palette(self):
        from ..i18n import get_setting
        css_vars = (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)
        own = not (accent_supported() and get_setting("system_accent", True))
        self.palette.load_from_string(palette_css(Adw.StyleManager.get_default().get_dark(), css_vars, own))
        # grafene tegnes med Cairo og må tegnes på nytt for å få de nye fargene
        for win in self.get_windows():
            stack = [win]
            while stack:
                w = stack.pop()
                if isinstance(w, Gtk.DrawingArea):
                    w.queue_draw()
                c = w.get_first_child()
                while c:
                    stack.append(c)
                    c = c.get_next_sibling()

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
