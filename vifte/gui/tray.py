"""Ikoner i systemstatusfeltet (StatusNotifierItem + DBusMenu over D-Bus).

GNOME viser disse med utvidelsen «AppIndicator and KStatusNotifierItem Support»;
KDE og de fleste andre skrivebord støtter dem direkte. GTK 4 har ikke eget API
for dette, så protokollen er implementert her med Gio.
"""
import os
import sys

import cairo
from gi.repository import Gio, GLib

WATCHER = "org.kde.StatusNotifierWatcher"

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""

_SNI_INFO = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
_MENU_INFO = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]


def render_value(text, color=(1, 1, 1), size=32):
    """Tegner en verdi (f.eks. «45») som ikon, slik FanControl gjør. Returnerer a(iiay)-data."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surf)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    fs = size * (0.78 if len(text) <= 2 else 0.58)
    cr.set_font_size(fs)
    ext = cr.text_extents(text)
    if ext.width > size - 1:
        cr.set_font_size(fs * (size - 1) / ext.width)
        ext = cr.text_extents(text)
    x = (size - ext.width) / 2 - ext.x_bearing
    y = (size - ext.height) / 2 - ext.y_bearing
    # tynn mørk kant gjør teksten lesbar på både lyst og mørkt panel
    cr.move_to(x, y)
    cr.text_path(text)
    cr.set_source_rgba(0, 0, 0, 0.55)
    cr.set_line_width(2)
    cr.stroke_preserve()
    cr.set_source_rgb(*color)
    cr.fill()
    surf.flush()
    data = bytes(surf.get_data())
    # Cairo lagrer piksler i maskinens byte-rekkefølge; SNI vil ha ARGB i nettverksrekkefølge.
    out = bytearray(len(data))
    if sys.byteorder == "little":
        out[0::4], out[1::4], out[2::4], out[3::4] = data[3::4], data[2::4], data[1::4], data[0::4]
    else:
        out[:] = data
    return [(size, size, bytes(out))]


def render_svg(path, color="#ffffff", size=32):
    """Tegner et (symbolsk) SVG-ikon i én farge som a(iiay)-data. Brukes som reserve
    hvis statusfeltet ikke finner ikonet ved navn."""
    from gi.repository import GdkPixbuf
    with open(path) as f:
        svg = f.read().replace("#2e3436", color)
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    pb = loader.get_pixbuf()
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    src = pb.get_pixels()
    out = bytearray(w * h * 4)
    for y in range(h):
        row = src[y * stride:y * stride + w * 4]
        o = y * w * 4
        out[o + 0:o + w * 4:4] = row[3::4]  # A
        out[o + 1:o + w * 4:4] = row[0::4]  # R
        out[o + 2:o + w * 4:4] = row[1::4]  # G
        out[o + 3:o + w * 4:4] = row[2::4]  # B
    return [(w, h, bytes(out))]


class Menu:
    """DBusMenu. Elementer: dict(label=..., action=callable, toggle=None|'checkmark'|'radio',
    state=bool, enabled=bool, children=[...]) eller dict(separator=True)."""

    def __init__(self, conn, path):
        self.conn, self.path = conn, path
        self.revision = 1
        self.items = {}
        self.root = []
        self.reg = conn.register_object(path, _MENU_INFO, self._call, self._get, None)

    def set(self, items):
        self.root = items
        self.items = {}
        self._number(items)
        self.revision += 1
        self.conn.emit_signal(None, self.path, "com.canonical.dbusmenu", "LayoutUpdated",
                              GLib.Variant("(ui)", (self.revision, 0)))

    def _number(self, items):
        for it in items:
            it["_id"] = len(self.items) + 1
            self.items[it["_id"]] = it
            self._number(it.get("children", []))

    @staticmethod
    def _props(it):
        if it is None:
            return {"children-display": GLib.Variant("s", "submenu")}
        if it.get("separator"):
            return {"type": GLib.Variant("s", "separator")}
        p = {"label": GLib.Variant("s", it["label"].replace("_", "__")),
             "enabled": GLib.Variant("b", it.get("enabled", True))}
        if it.get("toggle"):
            p["toggle-type"] = GLib.Variant("s", it["toggle"])
            p["toggle-state"] = GLib.Variant("i", 1 if it.get("state") else 0)
        if it.get("children"):
            p["children-display"] = GLib.Variant("s", "submenu")
        return p

    def _layout(self, ident, depth):
        it = self.items.get(ident)
        children = self.root if ident == 0 else (it or {}).get("children", [])
        kids = []
        if depth != 0:
            kids = [GLib.Variant("(ia{sv}av)", self._layout(c["_id"], depth - 1)) for c in children]
        return (ident, self._props(it), kids)

    def _call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            parent, depth, _names = params.unpack()
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, self._layout(parent, depth))))
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = [(i, self._props(self.items.get(i))) for i in ids if i in self.items or i == 0]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            inv.return_value(GLib.Variant("(v)", (self._props(self.items.get(i)).get(name, GLib.Variant("s", "")),)))
        elif method == "Event":
            i, event, _data, _ts = params.unpack()
            if event == "clicked":
                self._activate(i)
            inv.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for i, event, _d, _t in events:
                if event == "clicked":
                    self._activate(i)
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _activate(self, i):
        it = self.items.get(i)
        if it and it.get("action"):
            GLib.idle_add(lambda: it["action"]() and False)

    def _get(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def close(self):
        self.conn.unregister_object(self.reg)


class Item:
    """Ett ikon i statusfeltet."""

    def __init__(self, tray, path, item_id, title, icon_name=None):
        self.tray, self.path = tray, path
        self.id, self.title = item_id, title
        self.icon_name = icon_name or ""
        self.pixmap = []
        self.tooltip = title
        self.text = None
        self.reg = tray.conn.register_object(path, _SNI_INFO, self._call, self._get, None)

    def register(self):
        self.tray.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                            GLib.Variant("(s)", (self.path,)), None, Gio.DBusCallFlags.NONE, 2000,
                            None, None)

    def set_value(self, text, color, tooltip):
        if text != self.text:
            self.text = text
            self.pixmap = render_value(text, color)
            self._emit("NewIcon")
        if tooltip != self.tooltip:
            self.tooltip = tooltip
            self._emit("NewToolTip")

    def _emit(self, sig):
        self.tray.conn.emit_signal(None, self.path, "org.kde.StatusNotifierItem", sig, None)

    def _get(self, conn, sender, path, iface, prop):
        v = {
            "Category": ("s", "Hardware"), "Id": ("s", self.id), "Title": ("s", self.title),
            "Status": ("s", "Active"), "WindowId": ("i", 0), "IconName": ("s", self.icon_name),
            "IconThemePath": ("s", self.tray.icon_theme_path), "IconPixmap": ("a(iiay)", self.pixmap),
            "OverlayIconName": ("s", ""), "AttentionIconName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", (self.icon_name, [], self.title, self.tooltip)),
            "ItemIsMenu": ("b", False), "Menu": ("o", self.tray.menu.path),
        }.get(prop)
        return GLib.Variant(*v) if v else None

    def _call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(lambda: self.tray.on_activate() and False)
        inv.return_value(None)

    def close(self):
        # «Passive» skjuler ikonet før objektet forsvinner.
        self.tray.conn.emit_signal(None, self.path, "org.kde.StatusNotifierItem", "NewStatus",
                                   GLib.Variant("(s)", ("Passive",)))
        self.tray.conn.unregister_object(self.reg)


class Tray:
    """Hovedikonet + ett ikon per sensor i TrayIcons (som i FanControl)."""

    def __init__(self, on_activate, icon_name, icon_theme_path=""):
        self.on_activate = on_activate
        self.icon_name = icon_name
        self.icon_theme_path = icon_theme_path
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.menu = Menu(self.conn, "/no/vifte/Menu")
        self.main = Item(self, "/no/vifte/Tray", "vifte", "Vifte", icon_name)
        svg = os.path.join(icon_theme_path, "hicolor", "symbolic", "apps", icon_name + ".svg")
        try:
            self.main.pixmap = render_svg(svg) + render_svg(svg, size=64)
        except Exception:
            pass
        self.sensors = {}       # nøkkel -> Item
        self.available = False
        self._watch = Gio.bus_watch_name_on_connection(
            self.conn, WATCHER, Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)

    def _appeared(self, *_a):
        self.available = True
        self.main.register()
        for it in self.sensors.values():
            it.register()

    def _vanished(self, *_a):
        self.available = False

    def set_menu(self, items):
        self.menu.set(items)

    def set_sensors(self, wanted):
        """wanted: {nøkkel: (tittel, tekst, (r, g, b), verktøytips)}"""
        for key in list(self.sensors):
            if key not in wanted:
                self.sensors.pop(key).close()
        for n, (key, (title, text, color, tip)) in enumerate(wanted.items()):
            it = self.sensors.get(key)
            if it is None:
                safe = "".join(ch if ch.isalnum() else "_" for ch in key)[-60:]
                it = Item(self, f"/no/vifte/Sensor/s{n}_{safe}", f"vifte-{safe}", title)
                self.sensors[key] = it
                it.set_value(text, color, tip)
                if self.available:
                    it.register()
            else:
                it.set_value(text, color, tip)
