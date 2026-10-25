"""Kurveredigering og små diagrammer (Cairo)."""
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

PAD_L, PAD_R, PAD_T, PAD_B = 40, 14, 12, 28


def _color(widget, name, fallback):
    ok, rgba = widget.get_style_context().lookup_color(name)
    return rgba if ok else Gdk.RGBA(*fallback)


class CurveGraph(Gtk.DrawingArea):
    """Graf med punkter (temperatur, verdi). Kan redigeres med mus:
    venstreklikk legger til / drar punkt, høyreklikk fjerner punkt."""

    def __init__(self, editable=True, height=260):
        super().__init__()
        self.points = []
        self.x_range = (20.0, 100.0)
        self.y_range = (0.0, 100.0)
        self.x_unit, self.y_unit = "°C", "%"
        self.editable = editable
        self.selected = None
        self.marker = None        # (x, y) live posisjon
        self.on_changed = None    # kalles når redigering er ferdig
        self.on_select = None
        self.fahrenheit = False
        self._drag = None
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        if editable:
            click = Gtk.GestureClick(button=0)
            click.connect("pressed", self._pressed)
            self.add_controller(click)
            drag = Gtk.GestureDrag()
            drag.connect("drag-begin", self._drag_begin)
            drag.connect("drag-update", self._drag_update)
            drag.connect("drag-end", self._drag_end)
            self.add_controller(drag)

    # ---- koordinater
    def _area(self):
        w, h = self.get_width(), self.get_height()
        return PAD_L, PAD_T, max(1, w - PAD_L - PAD_R), max(1, h - PAD_T - PAD_B)

    def to_px(self, x, y):
        ax, ay, aw, ah = self._area()
        (x0, x1), (y0, y1) = self.x_range, self.y_range
        return ax + (x - x0) / (x1 - x0) * aw, ay + ah - (y - y0) / (y1 - y0) * ah

    def from_px(self, px, py):
        ax, ay, aw, ah = self._area()
        (x0, x1), (y0, y1) = self.x_range, self.y_range
        x = x0 + (px - ax) / aw * (x1 - x0)
        y = y0 + (ay + ah - py) / ah * (y1 - y0)
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def _hit(self, px, py):
        for i, (x, y) in enumerate(self.points):
            qx, qy = self.to_px(x, y)
            if (qx - px) ** 2 + (qy - py) ** 2 < 100:
                return i
        return None

    # ---- mus
    def _pressed(self, gesture, n, px, py):
        button = gesture.get_current_button()
        i = self._hit(px, py)
        if button == 3 and i is not None and len(self.points) > 1:
            del self.points[i]
            self.selected = None
            self._changed()
        elif button == 1 and i is None:
            x, y = self.from_px(px, py)
            self.points.append((round(x), round(y)))
            self.points.sort()
            self.selected = self.points.index((round(x), round(y)))
            self._changed()
        elif button == 1:
            self.selected = i
            if self.on_select:
                self.on_select(i)
        self.queue_draw()

    def _drag_begin(self, g, px, py):
        self._drag = self._hit(px, py)
        self._start = (px, py)

    def _drag_update(self, g, dx, dy):
        if self._drag is None:
            return
        x, y = self.from_px(self._start[0] + dx, self._start[1] + dy)
        x, y = round(x), round(y)
        # hold rekkefølgen: et punkt kan ikke passere naboene
        i = self._drag
        if i > 0:
            x = max(x, self.points[i - 1][0])
        if i < len(self.points) - 1:
            x = min(x, self.points[i + 1][0])
        self.points[i] = (x, y)
        self.selected = i
        if self.on_select:
            self.on_select(i)
        self.queue_draw()

    def _drag_end(self, g, dx, dy):
        if self._drag is not None and (abs(dx) > 1 or abs(dy) > 1):
            self._changed()
        self._drag = None

    def _changed(self):
        if self.on_changed:
            self.on_changed(self.points)
        if self.on_select:
            self.on_select(self.selected)

    # ---- tegning
    def _fmt_x(self, x):
        if self.x_unit == "°C" and self.fahrenheit:
            return f"{x * 9 / 5 + 32:.0f}°F"
        return f"{x:.0f}{self.x_unit}"

    def _draw(self, area, cr, w, h):
        fg = _color(self, "window_fg_color", (0.9, 0.9, 0.9, 1))
        accent = _color(self, "accent_bg_color", (0.2, 0.5, 0.9, 1))
        ax, ay, aw, ah = self._area()
        (x0, x1), (y0, y1) = self.x_range, self.y_range
        cr.set_line_width(1)
        cr.select_font_face("Sans")
        cr.set_font_size(11 if self.get_height() > 150 else 9.5)
        # rutenett
        def nice(span):
            raw = span / 6
            mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
            return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
        for val in _ticks(x0, x1, nice(x1 - x0)):
            px, _ = self.to_px(val, y0)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.move_to(px, ay)
            cr.line_to(px, ay + ah)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            t = self._fmt_x(val)
            ext = cr.text_extents(t)
            cr.move_to(px - ext.width / 2, ay + ah + 16)
            cr.show_text(t)
        for val in _ticks(y0, y1, nice(y1 - y0)):
            _, py = self.to_px(x0, val)
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.12)
            cr.move_to(ax, py)
            cr.line_to(ax + aw, py)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            t = f"{val:.0f}{self.y_unit}" if self.y_unit == "%" else f"{val:.0f}"
            ext = cr.text_extents(t)
            cr.move_to(ax - ext.width - 5, py + 4)
            cr.show_text(t)
        if not self.points:
            return
        # kurve (flat før første og etter siste punkt)
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
        cr.set_line_width(2.5)
        pts = sorted(self.points)
        cr.move_to(*self.to_px(x0, pts[0][1]))
        for x, y in pts:
            cr.line_to(*self.to_px(min(max(x, x0), x1), y))
        cr.line_to(*self.to_px(x1, pts[-1][1]))
        cr.stroke_preserve()
        cr.line_to(*self.to_px(x1, y0))
        cr.line_to(*self.to_px(x0, y0))
        cr.close_path()
        cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.12)
        cr.fill()
        if self.editable:
            for i, (x, y) in enumerate(self.points):
                px, py = self.to_px(x, y)
                cr.arc(px, py, 6 if i == self.selected else 4.5, 0, 2 * math.pi)
                cr.set_source_rgba(1, 1, 1, 1) if i == self.selected else cr.set_source_rgba(
                    accent.red, accent.green, accent.blue, 1)
                cr.fill_preserve()
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 1)
                cr.set_line_width(2)
                cr.stroke()
        if self.marker and self.marker[0] is not None:
            mx, my = self.marker
            px, _ = self.to_px(min(max(mx, x0), x1), y0)
            cr.set_source_rgba(0.95, 0.35, 0.3, 0.8)
            cr.set_line_width(1.5)
            cr.set_dash([4, 3])
            cr.move_to(px, ay)
            cr.line_to(px, ay + ah)
            cr.stroke()
            cr.set_dash([])
            if my is not None:
                _, py = self.to_px(x0, min(max(my, y0), y1))
                cr.arc(px, py, 5, 0, 2 * math.pi)
                cr.fill()


def _ticks(lo, hi, step):
    v = math.ceil(lo / step) * step
    out = []
    while v <= hi + 1e-9:
        out.append(v)
        v += step
    return out
