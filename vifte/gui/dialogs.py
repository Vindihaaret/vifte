"""Dialoger: kontrollinnstillinger, kurveredigering, egendefinerte sensorer, omkobling."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..config import (CURVE_TITLES, MIX_FUNCTIONS, SENSOR_TYPES, curve_type,  # noqa: E402
                      custom_sensor_type, format_point, hysteresis, parse_point, set_hysteresis)
from ..i18n import _  # noqa: E402
from .common import combo_row, entry_row, spin_row, switch_row  # noqa: E402
from .graph import CurveGraph  # noqa: E402


class LiveDialog(Adw.PreferencesDialog):
    """Basisklasse: holder seg oppdatert med verdier fra tjenesten."""

    def __init__(self, win, title):
        super().__init__(title=title)
        self.win = win
        self.model = win.model
        self.set_search_enabled(False)
        self.set_content_width(620)
        self.set_content_height(760)
        win.live_dialogs.add(self)
        self.connect("map", lambda *_: GLib.idle_add(lambda: self.set_focus(None)))
        self.connect("closed", lambda *_: win.live_dialogs.discard(self))

    def changed(self, rebuild_cards=True):
        self.win.config_changed(rebuild_cards)

    def update_live(self):
        pass


# ======================================================================= kontroller

class ControlDialog(LiveDialog):
    def __init__(self, win, ident):
        self.ident = ident
        self.model = win.model
        super().__init__(win, self.entry().get("NickName") or self.entry().get("Name") or ident)
        self.build()

    def entry(self):
        return self.model.cfg.control(self.ident) or {}

    def set(self, key, value, rebuild=True):
        self.entry()[key] = value
        self.changed(rebuild)

    def build(self):
        e = self.entry()
        cfg = self.model.cfg
        page = Adw.PreferencesPage()
        self.add(page)

        g = Adw.PreferencesGroup(title=_("Generelt"), description=self.ident)
        page.add(g)
        g.add(entry_row(_("Navn"), e.get("NickName") or e.get("Name"), lambda v: self.set("NickName", v)))
        g.add(switch_row(_("Aktivert"), e.get("Enable"), lambda v: self.set("Enable", v),
                         _("Når av, styrer BIOS/driveren viften selv")))
        curves = [(c.get("Name"), f"{c.get('Name')} ({_(CURVE_TITLES[curve_type(c)])})") for c in cfg.curves]
        g.add(combo_row(_("Kurve"), curves, (e.get("SelectedFanCurve") or {}).get("Name"),
                        lambda n: self.set("SelectedFanCurve", cfg.make_curve_ref(n) if n else None),
                        none_label="(ingen)"))
        g.add(switch_row(_("Skjult"), e.get("IsHidden"), lambda v: self.set("IsHidden", v)))

        g = Adw.PreferencesGroup(title=_("Manuell styring"))
        page.add(g)
        g.add(switch_row(_("Manuell styring"), e.get("ManualControl"), lambda v: self.set("ManualControl", v),
                         _("Overstyrer kurven med en fast verdi")))
        g.add(spin_row(_("Manuell verdi"), e.get("ManualControlValue", 50), 0, 100, 1,
                       lambda v: self.set("ManualControlValue", v, False)))

        g = Adw.PreferencesGroup(title=_("Justering"))
        page.add(g)
        g.add(spin_row(_("Forskyvning (offset) %"), e.get("SelectedOffset", 0), -100, 100, 1,
                       lambda v: self.set("SelectedOffset", v, False), subtitle=_("Legges til kurvens verdi")))
        g.add(spin_row(_("Minimum %"), e.get("MinimumPercent", 0), 0, 100, 1,
                       lambda v: self.set("MinimumPercent", v, False), subtitle=_("Går aldri under dette")))
        g.add(spin_row(_("Start %"), e.get("SelectedStart", 0), 0, 100, 1,
                       lambda v: self.set("SelectedStart", v, False),
                       subtitle=_("Brukes for å starte viften fra stillstand")))
        g.add(spin_row(_("Stopp %"), e.get("SelectedStop", 0), 0, 100, 1,
                       lambda v: self.set("SelectedStop", v, False), subtitle=_("Verdier under dette blir 0 %")))
        g.add(spin_row(_("Trinn opp %"), e.get("SelectedCommandStepUp", 8), 1, 100, 1,
                       lambda v: self.set("SelectedCommandStepUp", v, False),
                       subtitle=_("Maks økning per oppdatering")))
        g.add(spin_row(_("Trinn ned %"), e.get("SelectedCommandStepDown", 8), 1, 100, 1,
                       lambda v: self.set("SelectedCommandStepDown", v, False),
                       subtitle=_("Maks reduksjon per oppdatering")))
        g.add(switch_row(_("Tving bruk (Force apply)"), e.get("ForceApply"), lambda v: self.set("ForceApply", v, False),
                         _("Skriv verdien hver gang, også når den ikke er endret")))

        g = Adw.PreferencesGroup(title=_("Paring og kalibrering"))
        page.add(g)
        key = "MatchingFanSensor" if "MatchingFanSensor" in e else "PairedFanSensor"
        g.add(combo_row(_("Hastighetssensor"), self.model.fan_choices(), (e.get(key) or {}).get("Identifier"),
                        lambda i: self.set(key, cfg.make_ref(i, cfg.display_name(i)) if i else None),
                        none_label=_("(ikke paret)")))
        row = Adw.ActionRow(title=_("Kalibrering"),
                            subtitle=_("Måler RPM fra 0–100 % og finner start/stopp (tar ca. 2 min)"))
        btn = Gtk.Button(label=_("Kalibrer"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", self.calibrate)
        row.add_suffix(btn)
        g.add(row)
        self.calib = CurveGraph(editable=False, height=200)
        self.calib.y_unit = " RPM"
        self.calib.x_unit = "%"
        self.calib.x_range = (0, 100)
        g.add(self.calib)
        self.update_calibration()

    def update_calibration(self):
        cal = self.entry().get("Calibration") or []
        self.calib.points = [(float(p), float(r)) for p, r in cal]
        top = max([r for _, r in self.calib.points] + [1000])
        self.calib.y_range = (0, (int(top / 500) + 1) * 500)
        self.calib.set_visible(bool(cal))
        self.calib.queue_draw()

    def calibrate(self, _btn):
        try:
            self.model.client.call("calibrate", id=self.ident)
        except Exception as ex:
            self.win.toast(_("Kalibrering: {}").format(_(str(ex))))

    def update_live(self):
        st = (self.model.state.get("control_state") or {}).get(self.ident, {})
        if st.get("out") is not None and self.calib.points:
            self.calib.marker = (st["out"], st.get("rpm"))
            self.calib.queue_draw()
        if self.model.rev != getattr(self, "_rev", None):
            self._rev = self.model.rev
            self.update_calibration()


# ======================================================================= kurver

class CurveDialog(LiveDialog):
    def __init__(self, win, name):
        self.name = name
        super().__init__(win, name)
        self.graph = None
        self.build()

    def curve(self):
        return self.model.cfg.curve(self.name) or {}

    def set(self, key, value, rebuild=False):
        self.curve()[key] = value
        self.changed(rebuild)

    def rename(self, new):
        if not new or new == self.name:
            return
        if self.model.cfg.curve(new):
            self.win.toast(_("Det finnes allerede en kurve med det navnet"))
            return
        self.model.cfg.rename_curve(self.name, new)
        self.name = new
        self.set_title(new)
        self.changed(True)

    def build(self):
        c = self.curve()
        kind = curve_type(c)
        cfg = self.model.cfg
        page = Adw.PreferencesPage()
        self.add(page)
        g = Adw.PreferencesGroup(title=_("{}-kurve").format(_(CURVE_TITLES[kind])))
        page.add(g)
        g.add(entry_row(_("Navn"), self.name, self.rename))
        if "SelectedTempSource" in c:
            g.add(combo_row(_("Temperaturkilde"), self.model.temp_choices(),
                            (c.get("SelectedTempSource") or {}).get("Identifier"),
                            lambda i: self.set("SelectedTempSource",
                                               cfg.make_ref(i, cfg.display_name(i)) if i else None, True),
                            none_label=_("(velg sensor)")))
        if cfg.is_new_format and kind in ("graph", "linear", "flat", "mix", "trigger", "auto", "sync"):
            g.add(combo_row(_("Utdata"), [(0, _("Prosent (%)")), (1, _("RPM (krever kalibrering)"))],
                            int(c.get("CommandMode", 0) or 0), self.set_mode))
        self.live_row = Adw.ActionRow(title=_("Nå"))
        g.add(self.live_row)

        getattr(self, f"build_{kind}", self.build_unknown)(page, c)

        if kind in ("graph", "linear"):
            self.build_hysteresis(page, c)

        g = Adw.PreferencesGroup()
        page.add(g)
        btn = Gtk.Button(label=_("Slett kurve"), halign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.add_css_class("pill")
        btn.connect("clicked", self.delete)
        g.add(btn)

    def set_mode(self, mode):
        c = self.curve()
        c["CommandMode"] = mode
        if "MaximumCommand" in c or mode == 1:
            c["MaximumCommand"] = 100 if mode == 0 else max(c.get("MaximumCommand", 100), 2000)
        if self.graph:
            self.graph.y_range = (0, float(c.get("MaximumCommand", 100)))
            self.graph.y_unit = "%" if mode == 0 else " RPM"
            self.graph.queue_draw()
        self.changed(False)

    # ---- graf
    def build_graph(self, page, c):
        g = Adw.PreferencesGroup(title=_("Kurve"),
                                 description=_("Venstreklikk: legg til / dra punkt · Høyreklikk: fjern punkt"))
        page.add(g)
        self.graph = CurveGraph(editable=True, height=300)
        self.graph.fahrenheit = self.model.fahrenheit
        self.graph.points = sorted(parse_point(p) for p in c.get("Points") or [])
        self.graph.x_range = (float(c.get("MinimumTemperature", 20)), float(c.get("MaximumTemperature", 120)))
        if self.graph.x_range[1] <= self.graph.x_range[0]:
            self.graph.x_range = (20, 120)
        mode = int(c.get("CommandMode", 0) or 0)
        self.graph.y_range = (0, float(c.get("MaximumCommand", 100) or 100))
        self.graph.y_unit = "%" if mode == 0 else " RPM"
        self.graph.on_changed = self.points_changed
        self.graph.on_select = self.point_selected
        g.add(self.graph)

        box = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=8)
        self.pt_t = Gtk.SpinButton.new_with_range(-50, 200, 1)
        self.pt_v = Gtk.SpinButton.new_with_range(0, 100000, 1)
        for sb in (self.pt_t, self.pt_v):
            sb.set_digits(1)
            sb.set_width_chars(7)
        for lab, w in ((_("Temperatur °C"), self.pt_t), (_("Verdi"), self.pt_v)):
            box.append(Gtk.Label(label=lab))
            box.append(w)
        self._pt_lock = False
        self.pt_t.connect("value-changed", self.point_edited)
        self.pt_v.connect("value-changed", self.point_edited)
        g.add(box)
        self.point_selected(None)

        g = Adw.PreferencesGroup(title=_("Akse"))
        page.add(g)
        g.add(spin_row(_("Minimum temperatur"), self.graph.x_range[0], -50, 200, 1, lambda v: self.axis(0, v)))
        g.add(spin_row(_("Maksimum temperatur"), self.graph.x_range[1], -50, 250, 1, lambda v: self.axis(1, v)))

    def axis(self, which, v):
        c = self.curve()
        key = "MinimumTemperature" if which == 0 else "MaximumTemperature"
        c[key] = v
        lo, hi = float(c.get("MinimumTemperature", 20)), float(c.get("MaximumTemperature", 120))
        if hi > lo:
            self.graph.x_range = (lo, hi)
            self.graph.queue_draw()
        self.changed(False)

    def points_changed(self, pts):
        self.curve()["Points"] = [format_point(x, y) for x, y in sorted(pts)]
        self.changed(False)

    def point_selected(self, i):
        sens = i is not None and i < len(self.graph.points)
        self.pt_t.set_sensitive(sens)
        self.pt_v.set_sensitive(sens)
        if sens:
            self._pt_lock = True
            self.pt_t.set_value(self.graph.points[i][0])
            self.pt_v.set_value(self.graph.points[i][1])
            self._pt_lock = False

    def point_edited(self, *_):
        i = self.graph.selected
        if self._pt_lock or i is None or i >= len(self.graph.points):
            return
        self.graph.points[i] = (self.pt_t.get_value(), self.pt_v.get_value())
        self.graph.points.sort()
        self.graph.selected = self.graph.points.index((self.pt_t.get_value(), self.pt_v.get_value()))
        self.graph.queue_draw()
        self.points_changed(self.graph.points)

    # ---- lineær
    def build_linear(self, page, c):
        g = Adw.PreferencesGroup(title=_("Lineær"))
        page.add(g)
        for key, title, lo, hi in (("MinimumTemperature", _("Minimum temperatur °C"), -50, 200),
                                   ("MaximumTemperature", _("Maksimum temperatur °C"), -50, 200),
                                   ("MinimumFanSpeed", _("Minimum hastighet %"), 0, 100),
                                   ("MaximumFanSpeed", _("Maksimum hastighet %"), 0, 100)):
            g.add(spin_row(title, c.get(key, 0), lo, hi, 1, lambda v, k=key: self.set_preview(k, v)))
        self.preview = CurveGraph(editable=False, height=200)
        g.add(self.preview)
        self.set_preview(None, None)

    def set_preview(self, key, v):
        c = self.curve()
        if key:
            c[key] = v
            self.changed(False)
        t0, t1 = float(c.get("MinimumTemperature", 40)), float(c.get("MaximumTemperature", 80))
        self.preview.points = [(t0, float(c.get("MinimumFanSpeed", 20))), (t1, float(c.get("MaximumFanSpeed", 100)))]
        self.preview.x_range = (min(20, t0 - 10), max(100, t1 + 10))
        self.preview.fahrenheit = self.model.fahrenheit
        self.preview.queue_draw()

    # ---- flat
    def build_flat(self, page, c):
        g = Adw.PreferencesGroup(title=_("Fast verdi"))
        page.add(g)
        g.add(spin_row(_("Prosent"), c.get("Percent", 50), 0, 100, 1, lambda v: self.set("Percent", v)))

    # ---- mix
    def build_mix(self, page, c):
        g = Adw.PreferencesGroup(title=_("Mix"))
        page.add(g)
        g.add(combo_row(_("Funksjon"), list(enumerate(_(f) for f in MIX_FUNCTIONS)), int(c.get("SelectedMixFunction", 0)),
                        lambda i: self.set("SelectedMixFunction", i)))
        g = Adw.PreferencesGroup(title=_("Kurver som inngår"))
        page.add(g)
        chosen = [r.get("Name") for r in c.get("SelectedFanCurves") or []]
        for other in self.model.cfg.curves:
            n = other.get("Name")
            if n == self.name:
                continue
            row = Adw.SwitchRow(title=n, subtitle=_(CURVE_TITLES[curve_type(other)]), active=n in chosen)
            row.connect("notify::active", lambda r, _p, n=n: self.toggle_mix(n, r.get_active()))
            g.add(row)

    def toggle_mix(self, n, on):
        c = self.curve()
        refs = [r for r in c.get("SelectedFanCurves") or [] if r.get("Name") != n]
        if on:
            refs.append(self.model.cfg.make_curve_ref(n))
        c["SelectedFanCurves"] = refs
        self.changed(False)

    # ---- trigger
    def build_trigger(self, page, c):
        g = Adw.PreferencesGroup(title=_("Trigger"),
                                 description=_("Over lasttemperatur: lasthastighet til temperaturen faller "
                                             "under tomgangstemperatur, og omvendt."))
        page.add(g)
        for key, title, lo, hi in (("IdleTemperature", _("Tomgangstemperatur °C"), -50, 200),
                                   ("LoadTemperature", _("Lasttemperatur °C"), -50, 200),
                                   ("IdleFanSpeed", _("Tomgangshastighet %"), 0, 100),
                                   ("LoadFanSpeed", _("Lasthastighet %"), 0, 100),
                                   ("SelectedResponseTime", _("Responstid (s)"), 0, 600)):
            g.add(spin_row(title, c.get(key, 0), lo, hi, 1, lambda v, k=key: self.set(k, v)))

    # ---- synk
    def build_sync(self, page, c):
        g = Adw.PreferencesGroup(title=_("Synk"), description=_("Følger en annen kontroll sin utgang"))
        page.add(g)
        controls = [(x.get("Identifier"), x.get("NickName") or x.get("Name") or x.get("Identifier"))
                    for x in self.model.cfg.controls]
        g.add(combo_row(_("Kontroll"), controls, (c.get("SelectedControl") or {}).get("Identifier"),
                        lambda i: self.set("SelectedControl", {"Identifier": i} if i else None),
                        none_label=_("(velg kontroll)")))
        key = "SelectedOffset" if "SelectedOffset" in c else "Offset"
        g.add(spin_row(_("Forskyvning"), c.get(key, 0), -100, 100, 1, lambda v: self.set(key, v)))
        g.add(switch_row(_("Proporsjonal"), c.get("Proportional"), lambda v: self.set("Proportional", v),
                         _("Forskyvning i prosent av verdien i stedet for absolutt")))

    # ---- auto
    def build_auto(self, page, c):
        g = Adw.PreferencesGroup(title=_("Auto"),
                                 description=_("Finner laveste hastighet som holder lasttemperaturen. "
                                             "Under lastsonen oppfører den seg som en lineær kurve."))
        page.add(g)
        for key, title, lo, hi, d in (("IdleTemperature", _("Tomgangstemperatur °C"), -50, 200, 0),
                                      ("LoadTemperature", _("Lasttemperatur (mål) °C"), -50, 200, 0),
                                      ("MinFanSpeed", _("Minimum hastighet %"), 0, 100, 0),
                                      ("MaxFanSpeed", _("Maksimum hastighet %"), 0, 100, 0),
                                      ("Step", _("Trinn %"), 0.5, 20, 1),
                                      ("Deadband", _("Dødbånd °C"), 0, 20, 1),
                                      ("SelectedResponseTime", _("Responstid (s)"), 1, 600, 0)):
            g.add(spin_row(title, c.get(key, 0), lo, hi, 0.5 if d else 1, lambda v, k=key: self.set(k, v),
                           digits=d))

    def build_unknown(self, page, c):
        g = Adw.PreferencesGroup(title=_("Ukjent kurvetype"),
                                 description=_("Denne kurvetypen kjennes ikke igjen. Den bevares uendret i filen."))
        page.add(g)

    # ---- hysterese
    def build_hysteresis(self, page, c):
        up, down, rtu, rtd, ign = hysteresis(c)
        new = "HysteresisConfig" in c
        g = Adw.PreferencesGroup(title=_("Hysterese og responstid"))
        page.add(g)
        self.hyst = {"up": up, "down": down, "rtu": rtu, "rtd": rtd, "ign": ign}

        def upd(k, v):
            self.hyst[k] = v
            h = self.hyst
            set_hysteresis(self.curve(), h["up"], h["down"], h["rtu"], h["rtd"], h["ign"], new_style=new)
            self.changed(False)
        if new:
            g.add(spin_row(_("Hysterese opp °C"), up, 0, 50, 0.5, lambda v: upd("up", v), digits=1))
            g.add(spin_row(_("Hysterese ned °C"), down, 0, 50, 0.5, lambda v: upd("down", v), digits=1))
            g.add(spin_row(_("Responstid opp (s)"), rtu, 0, 600, 1, lambda v: upd("rtu", v)))
            g.add(spin_row(_("Responstid ned (s)"), rtd, 0, 600, 1, lambda v: upd("rtd", v)))
            g.add(switch_row(_("Ignorer hysterese ved min/maks"), ign, lambda v: upd("ign", v)))
        else:
            old = {"h": down, "rt": rtd, "one": up == 0 and down > 0}

            def upd_old(k, v):
                old[k] = v
                set_hysteresis(self.curve(), 0 if old["one"] else old["h"], old["h"], old["rt"], old["rt"],
                               False, new_style=False)
                self.changed(False)
            g.add(spin_row(_("Hysterese °C"), down, 0, 50, 0.5, lambda v: upd_old("h", v), digits=1))
            g.add(spin_row(_("Responstid (s)"), rtd, 0, 600, 1, lambda v: upd_old("rt", v)))
            g.add(switch_row(_("Kun én vei (bare ved synkende temperatur)"), old["one"],
                             lambda v: upd_old("one", v)))

    def delete(self, _b):
        dlg = Adw.AlertDialog(heading=_("Slette «{}»?").format(self.name),
                              body=_("Kontroller som bruker kurven mister den."))
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("delete", _("Slett"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def resp(_d, r):
            if r == "delete":
                self.model.cfg.delete_curve(self.name)
                self.changed(True)
                self.close()
        dlg.connect("response", resp)
        dlg.present(self)

    def update_live(self):
        st = (self.model.state.get("curves") or {}).get(self.name) or {}
        v, t = st.get("value"), st.get("input")
        mode = int(self.curve().get("CommandMode", 0) or 0)
        unit = " RPM" if mode == 1 else " %"
        parts = []
        if t is not None:
            parts.append(self.model.fmt_temp(t))
        parts.append("–" if v is None else f"{v:.0f}{unit}")
        self.live_row.set_subtitle("  →  ".join(parts))
        if self.graph:
            self.graph.marker = (t, v)
            self.graph.queue_draw()


# ======================================================================= egendefinerte sensorer

class CustomSensorDialog(LiveDialog):
    def __init__(self, win, ident):
        self.ident = ident
        self.model = win.model
        super().__init__(win, self.sensor().get("Name") or ident)
        self.set_content_height(560)
        self.build()

    def sensor(self):
        for s in self.model.cfg.custom_sensors:
            if s.get("Identifier") == self.ident:
                return s
        return {}

    def set(self, key, value):
        self.sensor()[key] = value
        self.changed(True)

    def build(self):
        s = self.sensor()
        kind = custom_sensor_type(s)
        cfg = self.model.cfg
        page = Adw.PreferencesPage()
        self.add(page)
        g = Adw.PreferencesGroup(title=_(SENSOR_TYPES[kind]), description=self.ident)
        page.add(g)
        self.live_row = Adw.ActionRow(title=_("Verdi nå"))
        g.add(self.live_row)
        choices = self.model.temp_choices(exclude=self.ident)
        if kind == "mix":
            g.add(combo_row(_("Funksjon"), list(enumerate(_(f) for f in MIX_FUNCTIONS)), int(s.get("SelectedMixFunction", 0)),
                            lambda i: self.set("SelectedMixFunction", i)))
            g2 = Adw.PreferencesGroup(title=_("Sensorer som inngår"))
            page.add(g2)
            chosen = [x.get("Identifier") for x in s.get("SelectedSensors") or []]
            for ident, label in choices:
                row = Adw.SwitchRow(title=label, active=ident in chosen)
                row.connect("notify::active", lambda r, _p, i=ident: self.toggle(i, r.get_active()))
                g2.add(row)
        elif kind == "time":
            g.add(combo_row(_("Kilde"), choices, (s.get("SelectedTempSource") or {}).get("Identifier"),
                            lambda i: self.set("SelectedTempSource", cfg.make_ref(i, cfg.display_name(i)) if i else None),
                            none_label=_("(velg sensor)")))
            g.add(spin_row(_("Periode (s)"), s.get("SelectedTime", 5), 1, 3600, 1, lambda v: self.set("SelectedTime", v)))
        elif kind == "file":
            g.add(entry_row(_("Fil (.sensor)"), s.get("FileName"), lambda v: self.set("FileName", v)))
            g.add(Adw.ActionRow(title=_("Format"), subtitle=_("Første linje i filen: temperatur i °C som tekst. "
                                "Relative navn leses fra /var/lib/vifte/sensors/")))
        else:
            g.add(combo_row(_("Kilde"), choices, (s.get("SelectedTempSource") or {}).get("Identifier"),
                            lambda i: self.set("SelectedTempSource", cfg.make_ref(i, cfg.display_name(i)) if i else None),
                            none_label=_("(velg sensor)")))
            g.add(spin_row(_("Forskyvning"), s.get("Offset", 0), -100, 100, 0.5, lambda v: self.set("Offset", v), digits=1))
            g.add(switch_row(_("Proporsjonal (%)"), s.get("Proportional"), lambda v: self.set("Proportional", v)))
        g = Adw.PreferencesGroup()
        page.add(g)
        btn = Gtk.Button(label=_("Slett sensor"), halign=Gtk.Align.CENTER)
        btn.add_css_class("destructive-action")
        btn.add_css_class("pill")
        btn.connect("clicked", self.delete)
        g.add(btn)

    def toggle(self, ident, on):
        s = self.sensor()
        lst = [x for x in s.get("SelectedSensors") or [] if x.get("Identifier") != ident]
        if on:
            custom = next((c for c in self.model.cfg.custom_sensors if c.get("Identifier") == ident), None)
            lst.append({"Identifier": ident, "Name": custom.get("Name")} if custom else
                       self.model.cfg.make_ref(ident, self.model.cfg.display_name(ident)))
        s["SelectedSensors"] = lst
        self.changed(True)

    def delete(self, _b):
        self.model.cfg.main["CustomSensors"] = [c for c in self.model.cfg.custom_sensors
                                                if c.get("Identifier") != self.ident]
        self.changed(True)
        self.close()

    def update_live(self):
        self.live_row.set_subtitle(self.model.fmt_temp(self.model.temp_value(self.ident)))


# ======================================================================= omkobling

class RemapDialog(Adw.PreferencesDialog):
    """Knytter identifikatorer fra en annen maskin (f.eks. Windows) til sensorer her."""

    def __init__(self, win):
        super().__init__(title=_("Manglende sensorer"))
        self.win = win
        self.set_content_width(700)
        model = win.model
        page = Adw.PreferencesPage()
        self.add(page)
        g = Adw.PreferencesGroup(
            title=_("Koble til sensorer på denne maskinen"),
            description=_("Konfigurasjonen refererer til maskinvare som ikke ble funnet. Velg hva hver "
                        "oppføring skal bruke her. Filen endres ikke, så den fungerer fortsatt i FanControl "
                        "på Windows."))
        page.add(g)
        st = model.state
        local = st.get("sensors", []) + [dict(c, kind="control") for c in st.get("controls", [])]
        aliases = st.get("aliases") or {}
        ids = sorted(set(st.get("missing") or []) | set(aliases))
        if not ids:
            g.add(Adw.ActionRow(title=_("Ingen manglende sensorer 🎉")))
        for ident in ids:
            kind = ident.rstrip("/").split("/")[-2] if ident.count("/") >= 2 else ""
            kind = {"sensor": "temperature"}.get(kind, kind)
            opts = [(s["id"], f"{s['hardware']} / {s['name']}") for s in local
                    if not kind or s["kind"] == kind or kind not in ("temperature", "fan", "control")]
            label = model.cfg.display_name(ident, ident)
            row = combo_row(label, opts, aliases.get(ident), lambda v, i=ident: self.set_alias(i, v),
                            none_label=_("(ikke koblet)"), subtitle=ident)
            g.add(row)

    def set_alias(self, config_id, local):
        try:
            self.win.model.client.call("set_alias", config_id=config_id, local=local)
        except Exception as ex:
            self.win.toast(_(str(ex)))


class RenameDialog:
    @staticmethod
    def ask(win, title, current, callback):
        dlg = Adw.AlertDialog(heading=title)
        entry = Gtk.Entry(text=current or "", activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Avbryt"))
        dlg.add_response("ok", _("OK"))
        dlg.set_default_response("ok")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and entry.get_text().strip() and
                    callback(entry.get_text().strip()))
        dlg.present(win)
        GLib.idle_add(entry.grab_focus)
