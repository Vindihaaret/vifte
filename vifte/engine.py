"""Reguleringsmotoren: sensorer -> kurver -> kontroller, én gang i sekundet."""
import logging
import math
import re
import os
import time
from collections import deque

from . import hw
from .config import (FanControlConfig, calibration_points, curve_type, custom_sensor_type, hysteresis,
                     parse_point)

log = logging.getLogger("vifte.engine")


def is_custom(ident):
    return bool(ident) and ident.split("/")[0] in ("Mix", "Time", "File", "Offset")


def mix(fn, values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    if fn == 0:
        return max(values)
    if fn == 1:
        return min(values)
    if fn == 2:
        return sum(values) / len(values)
    if fn == 3:
        return sum(values)
    if fn == 4:
        return values[0] - sum(values[1:])
    return max(values)


def interp(points, x):
    if not points:
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


class Hysteresis:
    """Holder på en «låst» temperatur og slipper bare gjennom endringer som er
    store nok (hysterese) og har vart lenge nok (responstid)."""

    def __init__(self):
        self.temp = None
        self.pending_since = None
        self.pending_dir = 0

    def filter(self, t, up, down, rt_up, rt_down, ignore_limits, lo, hi, now):
        if t is None:
            return self.temp
        if self.temp is None:
            self.temp = t
            return t
        if ignore_limits and (t >= hi or t <= lo):
            self.temp, self.pending_since = t, None
            return t
        d = t - self.temp
        direction = 1 if d >= up and d > 0 else -1 if -d >= down and d < 0 else 0
        if direction == 0:
            self.pending_since = None
            return self.temp
        if self.pending_dir != direction or self.pending_since is None:
            self.pending_dir, self.pending_since = direction, now
        wait = rt_up if direction > 0 else rt_down
        if now - self.pending_since >= wait - 0.05:
            self.temp, self.pending_since = t, None
        return self.temp


class CurveState:
    def __init__(self):
        self.hyst = Hysteresis()
        self.value = None
        self.input = None
        self.since = None
        self.auto_value = None
        self.last_step = 0.0
        self.prev_temp = None
        self.error = None


class Engine:
    def __init__(self, dry_run=False, hardware=None):
        self.dry_run = dry_run
        self._hardware = hardware  # (sensorer, kontroller) i tester; ellers skannes maskinen
        self.sensors, self.controls = hardware if hardware else hw.scan(dry_run)
        self.by_id = {}
        self.aliases = {}          # konfig-id -> lokal id (brukerens omkoblinger)
        self._index()
        self.cfg = FanControlConfig()
        self.curve_state = {}
        self.sensor_history = {}   # Time/xxx -> deque[(t, v)]
        self.custom_values = {}
        self.control_state = {}    # id -> dict(out, target, rpm ...)
        self.override = {}         # id -> prosent (kalibrering/paring)
        self.interval = 1.0
        self.paused = False
        # Nødmodus: over denne temperaturen (CPU/GPU) går alle styrte vifter på 100 %.
        self.failsafe_enabled = False
        self.failsafe_temp = 95.0
        self.failsafe_active = False
        self.failsafe_trigger = None   # (sensor-id, temperatur)
        # Sensortap: svarer ikke temperaturen til en kurve, går viftene på denne hastigheten.
        self.sensor_lost_enabled = True
        self.sensor_lost_speed = 80.0
        self._lost_since = {}          # kontroll-id -> når sensoren forsvant
        # Pumper (lokal kontroll-id -> {"mode": "bios"|"fixed", "speed": %}). Lagres på maskinen,
        # ikke i konfigurasjonen, og går foran alt konfigurasjonen sier om utgangen.
        self.pumps = {}

    def _index(self):
        self.by_id = {}
        for s in self.sensors + self.controls:
            self.by_id[s.id] = s
        for s in self.sensors + self.controls:
            for a in s.aliases:
                self.by_id.setdefault(a, s)

    def rescan(self):
        self.release_all()
        self.sensors, self.controls = self._hardware if self._hardware else hw.scan(self.dry_run)
        self._index()

    # ------------------------------------------------------------ oppslag
    def resolve(self, ident):
        """Finn lokal sensor/kontroll for en identifikator fra konfigurasjonen."""
        if not ident:
            return None
        ident = self.aliases.get(ident, ident)
        s = self.by_id.get(ident)
        if s:
            return s
        # NVApiWrapper/0-AD103-A/... : godta annen brikkekode for samme indeks
        if ident.startswith("NVApiWrapper/"):
            head, _, rest = ident[len("NVApiWrapper/"):].partition("/")
            idx = head.split("-")[0]
            for k, v in self.by_id.items():
                if k.startswith(f"NVApiWrapper/{idx}-") and k.endswith("/" + rest):
                    return v
        # Siste utvei: samme navn (fra listene i konfigurasjonen), men bare når navnet er
        # entydig – generiske navn som «Fan #1» gir ingen treff.
        e = self.cfg.sensor_entry(ident)
        if e:
            names = {n.replace("_", " ").lower() for n in (e.get("Name"), e.get("NickName"))
                     if n and not re.fullmatch(r"(temperature|fan|control|sensor) ?#?\d+", n.lower())}
            kind = ident.rsplit("/", 2)[-2] if ident.count("/") >= 2 else None
            kind = {"sensor": hw.TEMP, "temp": hw.TEMP}.get(kind, kind)
            hits = [s for s in self.sensors + self.controls
                    if s.name.replace("_", " ").lower() in names and (kind is None or kind == s.kind)]
            if len(hits) == 1:
                return hits[0]
        return None

    def missing_identifiers(self):
        refs = set()

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("SelectedTempSource", "PairedFanSensor", "MatchingFanSensor",
                             "SelectedControl", "SensorId") and isinstance(v, dict):
                        if v.get("Identifier"):
                            refs.add(v["Identifier"])
                    if k == "SelectedSensors" and isinstance(v, list):
                        refs.update(x.get("Identifier") for x in v if isinstance(x, dict))
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(self.cfg.main)
        for c in self.cfg.controls:
            if c.get("Enable"):
                refs.add(c.get("Identifier"))
        custom = {s.get("Identifier") for s in self.cfg.custom_sensors}
        return sorted(r for r in refs if r and r not in custom and self.resolve(r) is None)

    # ------------------------------------------------------------ sensorer
    def temperature(self, ref):
        if not ref:
            return None
        ident = ref.get("Identifier") if isinstance(ref, dict) else ref
        if ident in self.custom_values:
            return self.custom_values[ident]
        if is_custom(ident):
            return self._custom(ref if isinstance(ref, dict) else {"Identifier": ident})
        s = self.resolve(ident)
        if s is None:
            return None
        return s.value

    def _custom(self, s, depth=0):
        ident = s.get("Identifier")
        full = next((x for x in self.cfg.custom_sensors if x.get("Identifier") == ident), s)
        if depth > 8:
            return None
        if ident in self.custom_values:
            return self.custom_values[ident]
        kind = custom_sensor_type(full)
        v = None
        try:
            if kind == "mix":
                vals = []
                for sub in full.get("SelectedSensors") or []:
                    if is_custom(sub.get("Identifier")):
                        vals.append(self._custom(sub, depth + 1))
                    else:
                        vals.append(self.temperature(sub))
                v = mix(int(full.get("SelectedMixFunction", 0)), vals)
            elif kind == "time":
                src = full.get("SelectedTempSource")
                cur = self._custom(src, depth + 1) if src and is_custom(src.get("Identifier")) else self.temperature(src)
                hist = self.sensor_history.setdefault(ident, deque())
                now = time.monotonic()
                if cur is not None:
                    hist.append((now, cur))
                span = float(full.get("SelectedTime", 5))
                while hist and now - hist[0][0] > span:
                    hist.popleft()
                v = sum(x for _, x in hist) / len(hist) if hist else None
            elif kind == "file":
                fn = full.get("FileName") or ""
                path = fn if os.path.isabs(fn) else os.path.join(self.sensor_dir, fn)
                if not os.path.exists(path) and not path.endswith(".sensor"):
                    path += ".sensor"
                with open(path) as f:
                    v = float(f.readline().strip().replace(",", "."))
            else:  # offset
                src = full.get("SelectedTempSource")
                base = self._custom(src, depth + 1) if src and is_custom(src.get("Identifier")) else self.temperature(src)
                off = float(full.get("Offset", full.get("SelectedOffset", 0)) or 0)
                if base is not None:
                    v = base * (1 + off / 100.0) if full.get("Proportional") else base + off
        except Exception as e:
            log.debug("egendefinert sensor %s: %s", ident, e)
            v = None
        self.custom_values[ident] = v
        return v

    sensor_dir = "/var/lib/vifte/sensors"

    def lost_sources(self, ref, depth=0):
        """Sensorene bak en temperaturkilde som ikke gir verdi nå (tom liste = alt i orden).
        En mix-sensor regnes som tapt hvis én av sensorene den bygger på er borte, selv om
        de andre fortsatt svarer – den manglende kunne vært den varmeste."""
        ident = ref.get("Identifier") if isinstance(ref, dict) else ref
        if not ident or depth > 8:
            return []
        if is_custom(ident):
            full = next((x for x in self.cfg.custom_sensors if x.get("Identifier") == ident), None)
            if full is None:
                return [ident]
            kind = custom_sensor_type(full)
            if kind == "mix":
                return [i for sub in full.get("SelectedSensors") or [] for i in self.lost_sources(sub, depth + 1)]
            if kind in ("time", "offset"):
                return self.lost_sources(full.get("SelectedTempSource"), depth + 1)
            return [ident] if self.custom_values.get(ident) is None else []
        s = self.resolve(ident)
        if s is None or s.value is None or (isinstance(s.value, float) and math.isnan(s.value)):
            return [ident]
        return []

    def curve_lost(self, ref, stack=()):
        c = self.cfg.resolve_curve(ref)
        if c is None or c.get("Name") in stack:
            return []
        kind = curve_type(c)
        if kind == "mix":
            return [i for r in c.get("SelectedFanCurves") or []
                    for i in self.curve_lost(r, stack + (c.get("Name"),))]
        if kind in ("flat", "sync"):
            return []
        return self.lost_sources(c.get("SelectedTempSource"))

    # ------------------------------------------------------------ kurver
    def curve_value(self, ref, now, stack=()):
        c = self.cfg.resolve_curve(ref)
        if c is None:
            return None
        name = c.get("Name")
        if name in self._evaluated:
            return self._evaluated[name]
        if name in stack:
            return None  # sirkulær mix
        st = self.curve_state.setdefault(name, CurveState())
        kind = curve_type(c)
        v = None
        try:
            v = self._eval_curve(c, kind, st, now, stack + (name,))
            st.error = None
        except Exception as e:
            st.error = str(e)
            log.debug("kurve %s: %s", name, e)
        if v is not None:
            v = max(0.0, v)
        st.value = v
        self._evaluated[name] = v
        return v

    def _eval_curve(self, c, kind, st, now, stack):
        if kind == "flat":
            return float(c.get("Percent", 0))
        if kind == "mix":
            vals = [self.curve_value(r, now, stack) for r in c.get("SelectedFanCurves") or []]
            return mix(int(c.get("SelectedMixFunction", 0)), vals)
        if kind == "sync":
            ref = c.get("SelectedControl") or {}
            target = self.control_state.get(ref.get("Identifier"), {})
            base = target.get("out")
            if base is None:
                return None
            off = float(c.get("SelectedOffset", c.get("Offset", 0)) or 0)
            return base * (1 + off / 100.0) if c.get("Proportional") else base + off

        t = self.temperature(c.get("SelectedTempSource"))
        st.input = t
        if t is None:
            return st.value  # behold siste verdi hvis sensoren forsvinner et øyeblikk

        if kind == "graph":
            pts = sorted(parse_point(p) for p in c.get("Points") or [])
            if not pts:
                return None
            up, down, rtu, rtd, ign = hysteresis(c)
            tf = st.hyst.filter(t, up, down, rtu, rtd, ign, pts[0][0], pts[-1][0], now)
            return interp(pts, tf)

        if kind == "linear":
            t0, t1 = float(c.get("MinimumTemperature", 40)), float(c.get("MaximumTemperature", 80))
            v0, v1 = float(c.get("MinimumFanSpeed", 20)), float(c.get("MaximumFanSpeed", 100))
            up, down, rtu, rtd, ign = hysteresis(c)
            tf = st.hyst.filter(t, up, down, rtu, rtd, ign, t0, t1, now)
            return interp([(t0, v0), (t1, v1)], tf)

        if kind == "trigger":
            idle_t, load_t = float(c.get("IdleTemperature", 45)), float(c.get("LoadTemperature", 65))
            idle_v, load_v = float(c.get("IdleFanSpeed", 30)), float(c.get("LoadFanSpeed", 70))
            rt = float(c.get("SelectedResponseTime", 1) or 0)
            if st.value is None:
                st.value = load_v if t >= load_t else idle_v
            want = load_v if t >= load_t else idle_v if t <= idle_t else st.value
            if want != st.value:
                if st.since is None:
                    st.since = now
                if now - st.since >= rt - 0.05:
                    st.since = None
                    return want
                return st.value
            st.since = None
            return st.value

        if kind == "auto":
            return self._auto(c, st, t, now)
        return None

    AUTO_SAFETY_SPAN = 10.0   # °C over lasttemperaturen der Auto-kurven senest når 100 %

    def _auto(self, c, st, t, now):
        """Auto-kurve: under lastsonen lineær fra (tomgang, min) til (last, maks); i lastsonen
        en regulator som leter etter laveste hastighet som holder lasttemperaturen. Over
        lastsonen ligger den aldri under en linje som når 100 % ti grader over lasttemperaturen,
        så en plutselig varmeøkning ikke venter på regulatoren."""
        idle_t, load_t = float(c.get("IdleTemperature", 45)), float(c.get("LoadTemperature", 70))
        vmin, vmax = float(c.get("MinFanSpeed", 20)), float(c.get("MaxFanSpeed", 100))
        step, dead = float(c.get("Step", 2)), float(c.get("Deadband", 2))
        rt = float(c.get("SelectedResponseTime", 2) or 1)
        line = [(idle_t, vmin), (load_t, vmax)] if load_t > idle_t else [(load_t, vmax)]
        zone_lo = load_t - dead
        if t < zone_lo:
            st.auto_value, st.prev_temp = None, t
            return interp(line, t)
        if st.auto_value is None:
            st.auto_value = interp(line, zone_lo)
            st.last_step = now
            st.prev_temp = t
        falling = st.prev_temp is not None and t < st.prev_temp - 0.25
        wait = rt * (2 if falling else 1)
        if now - st.last_step >= wait:
            s = step / 2 if falling else step
            if t > load_t:
                st.auto_value += s
            elif t < zone_lo + dead / 2 or falling:
                st.auto_value -= s
            st.last_step, st.prev_temp = now, t
        if t > load_t:
            guard = interp([(load_t, interp(line, zone_lo)), (load_t + self.AUTO_SAFETY_SPAN, vmax)], t)
            st.auto_value = max(st.auto_value, guard)
        st.auto_value = max(vmin, min(vmax, st.auto_value))
        return st.auto_value

    # ------------------------------------------------------------ kontroller
    @staticmethod
    def rpm_to_percent(calibration, rpm):
        pts = sorted((r, p) for p, r in calibration_points(calibration))
        # fjern ikke-monotone punkter
        mono = []
        for r, p in pts:
            if not mono or r > mono[-1][0]:
                mono.append((r, p))
        return interp(mono, rpm) if mono else None

    def tick(self):
        now = time.monotonic()
        for s in self.sensors:
            s.update()
        for c in self.controls:
            c.update()
        self.custom_values = {}
        for cs in self.cfg.custom_sensors:
            self._custom(cs)
        self._evaluated = {}
        self._update_failsafe()

        for ctl_cfg in self.cfg.controls:
            ident = ctl_cfg.get("Identifier")
            ctl = self.resolve(ident)
            state = self.control_state.setdefault(ident, {"out": None, "target": None})
            paired = (ctl_cfg.get("PairedFanSensor") or ctl_cfg.get("MatchingFanSensor") or {}).get("Identifier")
            ps = self.resolve(paired) if paired else None
            state["rpm"] = ps.value if ps else None
            state["found"] = ctl is not None
            if ctl is None:
                continue
            state["read"] = ctl.value
            pump = self.pumps.get(ctl.id)
            state["pump"] = pump["mode"] if pump else None
            if pump:
                # Ingen kurver, kalibrering eller paring på en pumpe – bare BIOS eller fast hastighet.
                state["lost"] = None
                if pump["mode"] == "fixed" and not self.paused:
                    v = 100.0 if self.failsafe_active else max(self.PUMP_MIN, float(pump.get("speed", 100)))
                    self._apply(ctl, v, True)
                    state["out"] = state["target"] = v
                else:
                    if ctl.taken:
                        ctl.release()
                    state["out"] = state["target"] = None
                continue
            if self.failsafe_active and not self.paused and (ctl.id in self.override or ctl_cfg.get("Enable")):
                self._apply(ctl, 100.0, True)
                state["out"] = state["target"] = 100.0
                continue
            if ctl.id in self.override:
                self._apply(ctl, self.override[ctl.id], True)
                state["out"] = state["target"] = self.override[ctl.id]
                continue
            if not ctl_cfg.get("Enable") or self.paused:
                if ctl.taken:
                    ctl.release()
                state["out"] = state["target"] = state["lost"] = None
                self._lost_since.pop(ident, None)
                continue

            curve = self.cfg.resolve_curve(ctl_cfg.get("SelectedFanCurve"))
            lost = [] if ctl_cfg.get("ManualControl") or curve is None else \
                self.curve_lost(ctl_cfg.get("SelectedFanCurve"))
            if lost:
                since = self._lost_since.setdefault(ident, now)
            else:
                self._lost_since.pop(ident, None)
                if state.get("lost"):
                    log.warning("%s: sensoren svarer igjen, vanlig styring", ident)
            # Bare vifter Vifte allerede styrte; en sensor som aldri har virket lar BIOS styre.
            if lost and self.sensor_lost_enabled and state["out"] is not None \
                    and now - since >= self.SENSOR_LOST_AFTER:
                if not state.get("lost"):
                    log.warning("%s: sensor borte (%s), setter %.0f %%", ident, ", ".join(lost),
                                self.sensor_lost_speed)
                state["lost"] = sorted(set(lost))
                self._apply(ctl, self.sensor_lost_speed, True)
                state["out"] = state["target"] = self.sensor_lost_speed
                continue
            state["lost"] = None
            if ctl_cfg.get("ManualControl"):
                target = float(ctl_cfg.get("ManualControlValue", 50))
            elif curve is not None:
                target = self.curve_value(ctl_cfg.get("SelectedFanCurve"), now)
                if target is not None and int(curve.get("CommandMode", 0) or 0) == 1:
                    target = self.rpm_to_percent(ctl_cfg.get("Calibration"), target)
            else:
                target = None
            if target is None:
                # Ingen gyldig verdi: hold forrige, ellers la BIOS styre.
                if state["out"] is None:
                    if ctl.taken:
                        ctl.release()
                    continue
                target = state["target"] if state["target"] is not None else state["out"]
            state["target"] = target
            out = self.shape(ctl_cfg, target, state["out"])
            state["out"] = out
            self._apply(ctl, out, bool(ctl_cfg.get("ForceApply")))
        # Kurver som ikke brukes av noen kontroll vises likevel i GUI-et.
        for c in self.cfg.curves:
            self.curve_value({"Name": c.get("Name")}, now)
        return now

    FAILSAFE_HYSTERESIS = 7.0
    SENSOR_LOST_AFTER = 5.0   # sekunder uten verdi før sensoren regnes som borte
    PUMP_MIN = 70.0           # laveste faste hastighet for en pumpe

    def failsafe_sensors(self):
        """Kjernetemperaturer for CPU og GPU. NVMe, RAM o.l. er med vilje utelatt, og for
        AMD-skjermkort bare «edge» (junction kan normalt gå over 100 °C)."""
        out = []
        for s in self.sensors:
            if s.kind != hw.TEMP:
                continue
            if s.id.startswith(("/amdcpu/", "/intelcpu/", "NVApiWrapper/")) or (
                    s.id.startswith("/gpu-amd/") and s.name.lower() == "edge"):
                out.append(s)
        return out

    def _update_failsafe(self):
        if not self.failsafe_enabled:
            self.failsafe_active, self.failsafe_trigger = False, None
            return
        hottest = None
        for s in self.failsafe_sensors():
            if s.value is not None and (hottest is None or s.value > hottest[1]):
                hottest = (s.id, s.value)
        if hottest is None:
            return
        if hottest[1] >= self.failsafe_temp:
            if not self.failsafe_active:
                log.warning("NØDMODUS: %s er %.1f °C – alle styrte vifter går på 100 %%", *hottest)
            self.failsafe_active, self.failsafe_trigger = True, hottest
        elif self.failsafe_active and hottest[1] < self.failsafe_temp - self.FAILSAFE_HYSTERESIS:
            log.warning("nødmodus avsluttet (%s er %.1f °C)", *hottest)
            self.failsafe_active, self.failsafe_trigger = False, None
        elif self.failsafe_active:
            self.failsafe_trigger = hottest

    @staticmethod
    def shape(c, target, last):
        """Offset, stopp/start, minimum og trinnbegrensning – i samme rekkefølge som FanControl."""
        t = target + float(c.get("SelectedOffset", 0) or 0)
        t = max(0.0, min(100.0, t))
        stop = float(c.get("SelectedStop", 0) or 0)
        start = float(c.get("SelectedStart", 0) or 0)
        minimum = float(c.get("MinimumPercent", 0) or 0)
        if stop and t < stop:
            t = 0.0
        t = max(t, minimum)
        if last is None:
            return t
        if last <= 0 < t and start:
            return max(t, start)  # kickstart fra stillstand
        up = float(c.get("SelectedCommandStepUp", 100) or 100)
        down = float(c.get("SelectedCommandStepDown", 100) or 100)
        if t > last:
            t = min(t, last + up)
        elif t < last:
            t = max(t, last - down)
            if stop and 0 < t < stop:
                t = 0.0
        return round(t, 2)

    def _apply(self, ctl, pct, force):
        try:
            ctl.set(pct, force)
        except Exception as e:
            log.error("%s: kunne ikke sette %.0f %%: %s", ctl.id, pct, e)

    def release_all(self):
        for c in self.controls:
            c.release()

    # ------------------------------------------------------------ status til GUI
    def snapshot(self):
        sensors = [{"id": s.id, "name": s.name, "kind": s.kind, "hardware": s.hardware,
                    "value": None if s.value is None or (isinstance(s.value, float) and math.isnan(s.value))
                    else round(s.value, 2)} for s in self.sensors]
        controls = [{"id": c.id, "name": c.name, "hardware": c.hardware,
                     "value": None if c.value is None else round(c.value, 1)} for c in self.controls]
        curves = {n: {"value": None if st.value is None else round(st.value, 1),
                      "input": None if st.input is None else round(st.input, 1), "error": st.error}
                  for n, st in self.curve_state.items()}
        custom = {k: (None if v is None else round(v, 2)) for k, v in self.custom_values.items()}
        cstate = {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                  for k, v in self.control_state.items()}
        resolved = {}
        for entry in self.cfg.temp_sensors + self.cfg.fan_sensors + self.cfg.controls:
            ident = entry.get("Identifier")
            r = self.resolve(ident)
            resolved[ident] = r.id if r else None
        for ident in self.missing_identifiers():
            resolved[ident] = None
        return {"sensors": sensors, "controls": controls, "curves": curves, "custom": custom,
                "resolved": resolved,
                "failsafe": {"enabled": self.failsafe_enabled, "temp": self.failsafe_temp,
                             "active": self.failsafe_active, "trigger": self.failsafe_trigger},
                "sensor_lost": {"enabled": self.sensor_lost_enabled, "speed": self.sensor_lost_speed,
                                "controls": {k: v["lost"] for k, v in self.control_state.items() if v.get("lost")}},
                "control_state": cstate, "missing": self.missing_identifiers(),
                "aliases": self.aliases, "dry_run": self.dry_run, "paused": self.paused}
