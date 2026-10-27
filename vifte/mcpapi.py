"""Verktøyene Claude får via MCP, og sikkerhetsreglene for dem.

Alt her kjører i tjenesten (vifted), ikke i MCP-prosessen, slik at reglene gjelder
uansett hvem som kobler til. Prinsippene:

* Claude kan aldri få en styrt vifte til å stoppe: alt Claude setter eller endrer må
  holde utgangen på minst «gulvet» (standard 25 %), og stopp-% kan ikke ligge over det.
* Kurver Claude lager eller endrer må stige med temperaturen og nå 100 % senest ved
  nødtemperaturen. Bare kurvetyper som følger temperaturen (graf, lineær, auto, mix
  med maks/snitt) er tillatt.
* Nødmodus er alltid på mens Claude har tilgang: over nødtemperaturen går alle styrte
  vifter på 100 %, uansett konfigurasjon.
* Claude kan ikke slette konfigurasjoner, importere filer, pause styringen, endre
  oppdateringsintervallet, avinstallere eller endre sikkerhetsgrensene.
* Alle endringer kan angres (undo), og lagring tar alltid en sikkerhetskopi først.
"""
import copy
import json
import os
import time

from . import hw
from .config import (FanControlConfig, curve_type, custom_sensor_type, format_point, hysteresis,
                     new_curve, new_custom_sensor, parse_point, set_hysteresis)

MAX_HYST_UP = 5.0          # °C
MAX_RESPONSE_UP = 10.0     # s
MAX_HYST_DOWN = 15.0
MAX_RESPONSE_DOWN = 120.0
MIN_STEP_UP = 4.0          # % per oppdatering
OFFSET_RANGE = (-10.0, 50.0)
MAX_TIME_AVERAGE = 30.0    # s
ALLOWED_CURVES = ("graph", "linear", "auto", "mix")
MIX_NAMES = {"max": 0, "average": 2}


class McpError(RuntimeError):
    """Feil som vises til Claude, med en forklaring på hva som må endres."""


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise McpError(f"'{name}' must be a number") from None


class McpApi:
    def __init__(self, daemon):
        self.d = daemon

    # ================================================================== verktøyliste
    def tools(self):
        hyst = {
            "type": "object",
            "description": "Hysteresis/response time. Defaults: up 2 °C, down 3 °C, 1 s / 2 s.",
            "properties": {
                "up": {"type": "number", "description": f"°C rise needed before speeding up (max {MAX_HYST_UP})"},
                "down": {"type": "number", "description": f"°C drop needed before slowing down (max {MAX_HYST_DOWN})"},
                "response_up": {"type": "number", "description": f"seconds (max {MAX_RESPONSE_UP})"},
                "response_down": {"type": "number", "description": f"seconds (max {MAX_RESPONSE_DOWN})"},
                "ignore_at_limits": {"type": "boolean"},
            },
        }
        curve_props = {
            "temperature_source": {"type": "string",
                                   "description": "Sensor id from get_status (temperatures or custom sensors)"},
            "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                                  "minItems": 2, "maxItems": 2},
                       "description": "graph: [[temp_C, percent], ...], rising, ending at 100 %"},
            "min_temp": {"type": "number", "description": "linear: temperature where min_speed applies"},
            "max_temp": {"type": "number", "description": "linear: temperature where 100 % is reached"},
            "min_speed": {"type": "number", "description": "linear/auto: lowest speed in %"},
            "idle_temp": {"type": "number", "description": "auto: idle temperature"},
            "load_temp": {"type": "number", "description": "auto: target load temperature"},
            "step": {"type": "number", "description": "auto: step in % per adjustment"},
            "deadband": {"type": "number", "description": "auto: deadband in °C"},
            "response_time": {"type": "number", "description": "auto: seconds between adjustments"},
            "curves": {"type": "array", "items": {"type": "string"}, "description": "mix: names of curves"},
            "function": {"type": "string", "enum": list(MIX_NAMES), "description": "mix: max or average"},
            "hysteresis": hyst,
        }
        return [
            _tool("get_status",
                  "Current state: safety limits, every controllable fan (output %, RPM, curve, settings), "
                  "all temperatures, curves with live values, custom sensors, active configuration and "
                  "whether there are unsaved changes. Start here.", {}),
            _tool("get_history",
                  "Recorded temperatures (CPU/GPU and curve sources) and fan outputs/RPM, sampled every "
                  "5 s for the last 30 minutes. Use it to see how a change behaves over time.",
                  {"minutes": {"type": "number", "description": "1–30, default 10"},
                   "max_points": {"type": "integer", "description": "downsample to at most this many points (default 60)"}}),
            _tool("get_curve", "Full definition of one curve, including whether it passes the safety rules.",
                  {"name": {"type": "string"}}, ["name"]),
            _tool("create_curve",
                  "Create a fan curve. Allowed types: graph, linear, auto, mix (max/average). Curves must "
                  "rise with temperature, never go below the safety floor, and reach 100 % at or below "
                  "the emergency temperature.",
                  dict({"name": {"type": "string"},
                        "type": {"type": "string", "enum": list(ALLOWED_CURVES)}}, **curve_props),
                  ["name", "type"]),
            _tool("update_curve",
                  "Change an existing curve (only the given fields). The whole curve must pass the safety "
                  "rules afterwards – this also applies to curves the user made.",
                  dict({"name": {"type": "string"}, "new_name": {"type": "string"}}, **curve_props), ["name"]),
            _tool("delete_curve", "Delete a curve that no control or mix curve uses.",
                  {"name": {"type": "string"}}, ["name"]),
            _tool("configure_control",
                  "Configure one fan output. Set enabled=false to hand the fan back to the BIOS (always "
                  "allowed). Otherwise the result must keep the fan at or above the safety floor.",
                  {"control": {"type": "string", "description": "control id or name from get_status"},
                   "enabled": {"type": "boolean"},
                   "curve": {"type": "string", "description": "curve name"},
                   "manual": {"type": ["number", "null"],
                              "description": "fixed % (>= floor), or null to go back to the curve"},
                   "offset": {"type": "number", "description": f"% added to the curve ({OFFSET_RANGE[0]:g}–{OFFSET_RANGE[1]:g})"},
                   "minimum": {"type": "number", "description": "minimum %"},
                   "start": {"type": "number", "description": "kick-start %"},
                   "stop": {"type": "number", "description": "stop % (must not exceed the lowest output)"},
                   "step_up": {"type": "number", "description": f"max % increase per update (>= {MIN_STEP_UP:g})"},
                   "step_down": {"type": "number", "description": "max % decrease per update"},
                   "name": {"type": "string", "description": "display name"}},
                  ["control"]),
            _tool("create_custom_sensor",
                  "Create a virtual temperature sensor: max or average of several sensors, or a time average "
                  f"of one sensor (at most {MAX_TIME_AVERAGE:g} s).",
                  {"name": {"type": "string"},
                   "type": {"type": "string", "enum": ["max", "average", "time_average"]},
                   "sources": {"type": "array", "items": {"type": "string"}},
                   "seconds": {"type": "number"}},
                  ["name", "type", "sources"]),
            _tool("rename_sensor", "Give a temperature or fan sensor a friendlier display name.",
                  {"id": {"type": "string"}, "name": {"type": "string"}}, ["id", "name"]),
            _tool("pair_fans",
                  "Find which speed sensor belongs to which fan output by briefly running each at 100 %. "
                  "Takes about 15 s per output; poll get_task.", {}),
            _tool("calibrate_control",
                  "Measure RPM at different speeds for a paired control (never below the safety floor). "
                  "Takes about 1 minute; poll get_task.", {"control": {"type": "string"}}, ["control"]),
            _tool("get_task", "Progress and result of a running pairing or calibration.", {}),
            _tool("list_configurations", "Saved configurations (profiles).", {}),
            _tool("switch_configuration", "Activate another saved configuration made by the user.",
                  {"name": {"type": "string"}}, ["name"]),
            _tool("save_configuration",
                  "Save the active configuration (a backup of the previous file is kept). Only save when the "
                  "user is happy with the result.", {"name": {"type": "string", "description": "optional new name"}}),
            _tool("undo", "Undo the last change made through this interface.", {}),
        ]

    # ================================================================== kall
    def call(self, name, args):
        fn = getattr(self, f"t_{name}", None)
        if not name or fn is None:
            raise McpError(f"unknown tool: {name}")
        if not isinstance(args, dict):
            raise McpError("arguments must be an object")
        try:
            return fn(**args)
        except TypeError as ex:
            if "argument" in str(ex):
                raise McpError(f"invalid arguments for {name}: {ex}") from None
            raise

    # ------------------------------------------------------------------ hjelpere
    @property
    def floor(self):
        return float(self.d.settings["mcp_floor"])

    @property
    def emergency(self):
        return float(self.d.settings["failsafe_temp"])

    def _mutate(self, fn, template, *args):
        """Kjør fn(work) på en kopi; den kaster McpError hvis noe bryter reglene.
        Ellers tas kopien i bruk, og forrige tilstand legges på angre-stakken."""
        d = self.d
        with d.lock:
            before = copy.deepcopy(d.engine.cfg.raw)
            work = FanControlConfig(copy.deepcopy(before))
            result = fn(work)
            self.check_protected(FanControlConfig(before), work)
            d.commit_config(work)
            # Angre er bare trygt så lenge ingen andre har endret konfigurasjonen etterpå.
            d.undo_stack.append((before, d.rev))
            d.log_mcp(template, *args)
        return result

    def _control_entry(self, cfg, key):
        if not isinstance(key, str) or not key:
            raise McpError("control must be a control id or name")
        for c in cfg.controls:
            if c.get("Identifier") == key:
                return c
        hits = [c for c in cfg.controls
                if key.lower() in ((c.get("NickName") or "").lower(), (c.get("Name") or "").lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise McpError(f"'{key}' matches several controls – use the id")
        raise McpError(f"no control '{key}' – see get_status")

    # ------------------------------------------------------------------ beskyttede kontroller
    def _protected_entries(self, cfg, include_pumps=True):
        """{lokal id: kontrolloppføring} for pumper og kontroller brukeren har låst for Claude."""
        protected = self.d.protected_for_claude() if include_pumps else set(self.d.settings.get("claude_locked") or [])
        out = {}
        for c in cfg.controls:
            local = self.d.engine.resolve(c.get("Identifier"))
            if local is not None and local.id in protected:
                out[local.id] = c
        return out

    def _footprint(self, cfg, entry):
        """Alt som bestemmer hva en kontroll gjør: oppføringen, kurvene den bruker (også via mix og
        sync) og de egendefinerte sensorene bak dem."""
        seen_curves, seen_sensors, parts = set(), set(), [entry]

        def sensor(ref, depth=0):
            ident = (ref or {}).get("Identifier") if isinstance(ref, dict) else ref
            if not ident or ident in seen_sensors or depth > 8:
                return
            seen_sensors.add(ident)
            cs = next((x for x in cfg.custom_sensors if x.get("Identifier") == ident), None)
            if cs:
                parts.append(cs)
                for sub in cs.get("SelectedSensors") or []:
                    sensor(sub, depth + 1)
                sensor(cs.get("SelectedTempSource"), depth + 1)

        def curve(ref, depth=0):
            name = (ref or {}).get("Name") if isinstance(ref, dict) else ref
            if not name or name in seen_curves or depth > 8:
                return
            seen_curves.add(name)
            c = cfg.curve(name)
            parts.append(c)
            if not c:
                return
            for sub in c.get("SelectedFanCurves") or []:
                curve(sub, depth + 1)
            sensor(c.get("SelectedTempSource"))
            sync = (c.get("SelectedControl") or {}).get("Identifier")
            if sync:
                other = cfg.control(sync)
                if other:
                    curve(other.get("SelectedFanCurve"), depth + 1)
        curve(entry.get("SelectedFanCurve"))
        return json.dumps(parts, sort_keys=True, default=str)

    def check_protected(self, before, after, include_pumps=True):
        """Avvis enhver endring som påvirker en pumpe eller en låst kontroll – direkte eller
        indirekte via en kurve eller sensor den bruker."""
        old = self._protected_entries(before, include_pumps)
        new = self._protected_entries(after, include_pumps)
        for lid, entry in old.items():
            other = new.get(lid)
            if other is None or self._footprint(before, entry) != self._footprint(after, other):
                label = entry.get("NickName") or entry.get("Name") or entry.get("Identifier")
                kind = "a pump" if lid in (self.d.settings.get("pumps") or {}) else "locked by the user"
                raise McpError(f"'{label}' is {kind} – Claude can't change it, or the curves and sensors "
                               "it uses. Ask the user to change it in the Vifte app.")

    def _curve(self, cfg, name):
        c = cfg.curve(name)
        if c is None:
            raise McpError(f"no curve named '{name}'")
        return c

    def _sensor_ref(self, cfg, key):
        """Godtar id eller navn på en temperatursensor (også egendefinert)."""
        if not isinstance(key, str) or not key:
            raise McpError("temperature_source must be a sensor id")
        for cs in cfg.custom_sensors:
            if key in (cs.get("Identifier"), cs.get("Name")):
                return cs.get("Identifier")
        e = self.d.engine
        if any(t.get("Identifier") == key for t in cfg.temp_sensors) or e.resolve(key):
            return key
        hits = [t.get("Identifier") for t in cfg.temp_sensors
                if key.lower() in ((t.get("NickName") or "").lower(), (t.get("Name") or "").lower())]
        if len(hits) == 1:
            return hits[0]
        raise McpError(f"unknown or ambiguous temperature sensor '{key}' – use an id from get_status")

    # ------------------------------------------------------------------ sikkerhetsregler
    def check_source(self, cfg, ident, depth=0):
        if depth > 5:
            raise McpError("custom sensors are nested too deeply")
        custom = next((cs for cs in cfg.custom_sensors if cs.get("Identifier") == ident), None)
        if custom is not None:
            kind = custom_sensor_type(custom)
            if kind == "mix":
                if int(custom.get("SelectedMixFunction", 0)) not in MIX_NAMES.values():
                    raise McpError(f"custom sensor '{custom.get('Name')}' uses min/sum/subtract – "
                                   "only max or average may drive Claude-controlled fans")
                srcs = [x.get("Identifier") for x in custom.get("SelectedSensors") or []]
                if not srcs:
                    raise McpError(f"custom sensor '{custom.get('Name')}' has no sources")
                for src in srcs:
                    self.check_source(cfg, src, depth + 1)
                return
            if kind == "time":
                if float(custom.get("SelectedTime", 5)) > MAX_TIME_AVERAGE:
                    raise McpError(f"time average over {MAX_TIME_AVERAGE:g} s reacts too slowly")
                self.check_source(cfg, (custom.get("SelectedTempSource") or {}).get("Identifier"), depth + 1)
                return
            raise McpError(f"custom sensor type '{kind}' can't drive Claude-controlled fans")
        s = self.d.engine.resolve(ident)
        if s is None or s.kind != hw.TEMP:
            raise McpError(f"temperature source '{ident}' is not a temperature sensor on this machine")
        if s.value is None:
            raise McpError(f"temperature source '{ident}' has no reading right now")

    def check_hysteresis(self, c):
        up, down, rtu, rtd, _ign = hysteresis(c)
        if up > MAX_HYST_UP or rtu > MAX_RESPONSE_UP:
            raise McpError(f"curve '{c.get('Name')}' reacts too slowly to rising temperature "
                           f"(max hysteresis up {MAX_HYST_UP:g} °C, max response up {MAX_RESPONSE_UP:g} s)")
        if down > MAX_HYST_DOWN or rtd > MAX_RESPONSE_DOWN:
            raise McpError(f"curve '{c.get('Name')}': hysteresis down max {MAX_HYST_DOWN:g} °C, "
                           f"response down max {MAX_RESPONSE_DOWN:g} s")

    def check_curve(self, cfg, c, depth=0):
        """Returnerer laveste utgang kurven kan gi. Kaster McpError hvis den bryter reglene."""
        name = c.get("Name")
        kind = curve_type(c)
        floor, emergency = self.floor, self.emergency
        if depth > 5:
            raise McpError("mix curves are nested too deeply")
        if kind not in ALLOWED_CURVES:
            raise McpError(f"curve '{name}' is a {kind} curve – Claude-controlled fans may only use "
                           f"{', '.join(ALLOWED_CURVES)} curves")
        if kind != "mix" and int(c.get("CommandMode", 0) or 0) != 0:
            raise McpError(f"curve '{name}' uses RPM mode – only percent curves are allowed")
        if kind == "mix":
            if int(c.get("SelectedMixFunction", 0)) not in MIX_NAMES.values():
                raise McpError(f"mix curve '{name}' must use max or average")
            refs = c.get("SelectedFanCurves") or []
            if not refs:
                raise McpError(f"mix curve '{name}' has no curves")
            mins = [self.check_curve(cfg, self._curve(cfg, r.get("Name")), depth + 1) for r in refs]
            return max(mins) if int(c.get("SelectedMixFunction", 0)) == 0 else sum(mins) / len(mins)

        self.check_source(cfg, (c.get("SelectedTempSource") or {}).get("Identifier"))
        if kind == "graph":
            pts = sorted(parse_point(p) for p in c.get("Points") or [])
            if len(pts) < 2:
                raise McpError(f"curve '{name}' needs at least two points")
            temps = [t for t, _ in pts]
            if len(set(temps)) != len(temps):
                raise McpError(f"curve '{name}' has two points at the same temperature")
            for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                if v1 < v0:
                    raise McpError(f"curve '{name}' goes down from {v0:g} % to {v1:g} % between "
                                   f"{t0:g} and {t1:g} °C – speed must never fall as temperature rises")
            low = pts[0][1]
            if low < floor:
                raise McpError(f"curve '{name}' starts at {low:g} %, below the safety floor of {floor:g} %")
            hit = next((t for t, v in pts if v >= 100), None)
            if hit is None:
                raise McpError(f"curve '{name}' never reaches 100 %")
            if hit > emergency:
                raise McpError(f"curve '{name}' reaches 100 % at {hit:g} °C – it must do so at or below "
                               f"the emergency temperature ({emergency:g} °C)")
            self.check_hysteresis(c)
            return low
        if kind == "linear":
            t0, t1 = float(c.get("MinimumTemperature", 40)), float(c.get("MaximumTemperature", 80))
            v0, v1 = float(c.get("MinimumFanSpeed", 20)), float(c.get("MaximumFanSpeed", 100))
            if t0 >= t1:
                raise McpError(f"curve '{name}': min_temp must be below max_temp")
            if v0 < floor:
                raise McpError(f"curve '{name}': min_speed {v0:g} % is below the safety floor of {floor:g} %")
            if v1 < 100:
                raise McpError(f"curve '{name}' must end at 100 %")
            if t1 > emergency:
                raise McpError(f"curve '{name}': max_temp must be at or below the emergency temperature "
                               f"({emergency:g} °C)")
            self.check_hysteresis(c)
            return v0
        # auto
        idle, load = float(c.get("IdleTemperature", 45)), float(c.get("LoadTemperature", 70))
        vmin, vmax = float(c.get("MinFanSpeed", 20)), float(c.get("MaxFanSpeed", 100))
        if idle >= load:
            raise McpError(f"curve '{name}': idle_temp must be below load_temp")
        if vmin < floor:
            raise McpError(f"curve '{name}': min_speed {vmin:g} % is below the safety floor of {floor:g} %")
        if vmax < 100:
            raise McpError(f"auto curve '{name}' must allow 100 %")
        if load > emergency:
            raise McpError(f"curve '{name}': load_temp must be at or below the emergency temperature")
        if float(c.get("Step", 2)) < 1 or float(c.get("SelectedResponseTime", 2)) > MAX_RESPONSE_UP:
            raise McpError(f"auto curve '{name}' adjusts too slowly (step >= 1 %, response time <= "
                           f"{MAX_RESPONSE_UP:g} s)")
        return vmin

    def check_control(self, cfg, c):
        """Et aktivert kontrollpunkt må aldri kunne gi under gulvet."""
        if not c.get("Enable"):
            return
        floor = self.floor
        offset = float(c.get("SelectedOffset", 0) or 0)
        minimum = float(c.get("MinimumPercent", 0) or 0)
        stop = float(c.get("SelectedStop", 0) or 0)
        step_up = float(c.get("SelectedCommandStepUp", 8) or 0)
        label = c.get("NickName") or c.get("Name") or c.get("Identifier")
        if not OFFSET_RANGE[0] <= offset <= OFFSET_RANGE[1]:
            raise McpError(f"{label}: offset must be between {OFFSET_RANGE[0]:g} and {OFFSET_RANGE[1]:g} %")
        if step_up < MIN_STEP_UP:
            raise McpError(f"{label}: step_up must be at least {MIN_STEP_UP:g} % so the fan can react quickly")
        if c.get("ManualControl"):
            low = float(c.get("ManualControlValue", 0))
        else:
            ref = c.get("SelectedFanCurve")
            if not ref:
                raise McpError(f"{label} has no curve – assign one or set enabled=false")
            low = self.check_curve(cfg, self._curve(cfg, ref.get("Name")))
        effective = max(min(100.0, low + offset), minimum)
        if effective < floor:
            raise McpError(f"{label} could run at {effective:g} %, below the safety floor of {floor:g} % "
                           "(check offset, minimum and the curve)")
        if stop > effective:
            raise McpError(f"{label}: stop {stop:g} % is above the lowest output ({effective:g} %) and could "
                           "stop the fan – lower stop")

    def _users_of(self, cfg, name):
        ctrls = [c for c in cfg.controls if (c.get("SelectedFanCurve") or {}).get("Name") == name]
        mixes = [m for m in cfg.curves if any(r.get("Name") == name for r in m.get("SelectedFanCurves") or [])]
        return ctrls, mixes

    # ------------------------------------------------------------------ lese
    def t_get_status(self):
        d, e = self.d, self.d.engine
        with d.lock:
            cfg = e.cfg
            snap = e.snapshot()
            local = {s["id"]: s for s in snap["sensors"]}
            temps, fans = [], []
            for lst, out in ((cfg.temp_sensors, temps), (cfg.fan_sensors, fans)):
                for entry in lst:
                    ident = entry.get("Identifier")
                    lid = snap["resolved"].get(ident)
                    s = local.get(lid)
                    item = {"id": ident, "name": entry.get("NickName") or entry.get("Name") or ident,
                            "hardware": s["hardware"] if s else "missing",
                            "value": s["value"] if s else None}
                    if lst is cfg.temp_sensors and s and lid in {x.id for x in e.failsafe_sensors()}:
                        item["core_temperature"] = True
                    out.append(item)
            controls = []
            for c in cfg.controls:
                ident = c.get("Identifier")
                st = snap["control_state"].get(ident, {})
                if not st.get("found"):
                    continue
                controls.append({
                    "id": ident, "name": c.get("NickName") or c.get("Name"),
                    "enabled": bool(c.get("Enable")), "output_percent": st.get("out"),
                    "rpm": st.get("rpm"),
                    "paired_fan": (c.get("PairedFanSensor") or c.get("MatchingFanSensor") or {}).get("Identifier"),
                    "curve": (c.get("SelectedFanCurve") or {}).get("Name"),
                    "manual": c.get("ManualControlValue") if c.get("ManualControl") else None,
                    "offset": c.get("SelectedOffset", 0), "minimum": c.get("MinimumPercent", 0),
                    "start": c.get("SelectedStart", 0), "stop": c.get("SelectedStop", 0),
                    "step_up": c.get("SelectedCommandStepUp"), "step_down": c.get("SelectedCommandStepDown"),
                    "calibrated": bool(c.get("Calibration")),
                    "pump": self._pump_mode(ident),
                    "claude_can_change": not self._is_protected(ident),
                    "meets_safety_rules": self._ok(lambda c=c: self.check_control(cfg, c)),
                })
            curves = []
            for c in cfg.curves:
                st = snap["curves"].get(c.get("Name"), {})
                curves.append({"name": c.get("Name"), "type": curve_type(c),
                               "source": (c.get("SelectedTempSource") or {}).get("Identifier"),
                               "input": st.get("input"), "output": st.get("value"),
                               "meets_safety_rules": self._ok(lambda c=c: self.check_curve(cfg, c))})
            custom = [{"id": cs.get("Identifier"), "name": cs.get("Name"), "type": custom_sensor_type(cs),
                       "value": snap["custom"].get(cs.get("Identifier"))} for cs in cfg.custom_sensors]
            return {
                "configuration": d.settings["current"], "unsaved_changes": d.dirty,
                "paused": e.paused,
                "safety": {"floor_percent": self.floor, "emergency_temperature": self.emergency,
                           "emergency_active": e.failsafe_active, "emergency_trigger": e.failsafe_trigger,
                           "sensor_lost_protection": e.sensor_lost_enabled,
                           "sensor_lost_speed_percent": e.sensor_lost_speed,
                           "controls_with_lost_sensor": snap["sensor_lost"]["controls"],
                           "rules": "Fans you configure can never go below floor_percent; curves must rise "
                                    "and reach 100 % by emergency_temperature; above it all fans run at 100 %. "
                                    "If a curve's sensor stops responding, its fans run at "
                                    "sensor_lost_speed_percent until it is back."},
                "controls": controls, "curves": curves, "temperatures": temps, "fans": fans,
                "custom_sensors": custom, "missing_hardware": snap["missing"],
            }

    def _pump_mode(self, ident):
        local = self.d.engine.resolve(ident)
        return ((self.d.settings.get("pumps") or {}).get(local.id) or {}).get("mode") if local else None

    def _is_protected(self, ident):
        local = self.d.engine.resolve(ident)
        return bool(local) and local.id in self.d.protected_for_claude()

    @staticmethod
    def _ok(fn):
        try:
            fn()
            return True
        except McpError as ex:
            return str(ex)

    def t_get_history(self, minutes=10, max_points=60):
        minutes = max(1.0, min(30.0, _num(minutes, "minutes")))
        max_points = int(max(5, min(360, _num(max_points, "max_points"))))
        cutoff = time.time() - minutes * 60
        with self.d.lock:
            rows = [r for r in self.d.history if r["t"] >= cutoff]
        if len(rows) > max_points:
            step = len(rows) / max_points
            rows = [rows[int(i * step)] for i in range(max_points)]
        temps, outs = {}, {}
        for i, r in enumerate(rows):
            for k, v in r["temps"].items():
                temps.setdefault(k, [None] * len(rows))[i] = v
            for k, (o, rpm) in r["controls"].items():
                outs.setdefault(k, {"percent": [None] * len(rows), "rpm": [None] * len(rows)})
                outs[k]["percent"][i] = o
                outs[k]["rpm"][i] = rpm
        return {"seconds_ago": [round(time.time() - r["t"]) for r in rows], "temperatures": temps,
                "controls": outs, "note": "Samples are taken every 5 s; history covers at most 30 min."}

    def t_get_curve(self, name):
        cfg = self.d.engine.cfg
        c = copy.deepcopy(self._curve(cfg, name))
        return {"type": curve_type(c), "definition": c, "used_by": [
            x.get("NickName") or x.get("Identifier") for x in self._users_of(cfg, name)[0]],
            "meets_safety_rules": self._ok(lambda: self.check_curve(cfg, c))}

    # ------------------------------------------------------------------ kurver
    def _apply_curve_fields(self, cfg, c, a):
        kind = curve_type(c)
        if "temperature_source" in a:
            if kind == "mix":
                raise McpError("mix curves take 'curves', not a temperature source")
            ident = self._sensor_ref(cfg, a["temperature_source"])
            c["SelectedTempSource"] = cfg.make_ref(ident, cfg.display_name(ident))
        if "points" in a:
            if kind != "graph":
                raise McpError("'points' only applies to graph curves")
            try:
                pts = sorted((float(t), float(v)) for t, v in a["points"])
            except (TypeError, ValueError):
                raise McpError("points must be [[temperature, percent], ...]") from None
            if any(not 0 <= v <= 100 for _, v in pts) or any(not -20 <= t <= 150 for t, _ in pts):
                raise McpError("points must be within 0–100 % and -20–150 °C")
            c["Points"] = [format_point(t, v) for t, v in pts]
            lo, hi = pts[0][0], pts[-1][0]
            c["MinimumTemperature"] = min(float(c.get("MinimumTemperature", 20)), max(0, lo - 10))
            c["MaximumTemperature"] = max(float(c.get("MaximumTemperature", 100)), hi + 10)
        mapping = {"linear": {"min_temp": "MinimumTemperature", "max_temp": "MaximumTemperature",
                              "min_speed": "MinimumFanSpeed"},
                   "auto": {"idle_temp": "IdleTemperature", "load_temp": "LoadTemperature",
                            "min_speed": "MinFanSpeed", "step": "Step", "deadband": "Deadband",
                            "response_time": "SelectedResponseTime"}}.get(kind, {})
        for key, field in mapping.items():
            if key in a:
                c[field] = _num(a[key], key)
        if kind == "linear":
            c["MaximumFanSpeed"] = 100
        if kind == "auto":
            c["MaxFanSpeed"] = 100
        if kind == "mix":
            if "curves" in a:
                names = a["curves"]
                if not isinstance(names, list) or not names:
                    raise McpError("'curves' must be a non-empty list of curve names")
                for n in names:
                    if n == c.get("Name"):
                        raise McpError("a mix curve can't include itself")
                    self._curve(cfg, n)
                c["SelectedFanCurves"] = [cfg.make_curve_ref(n) for n in names]
            if "function" in a:
                if a["function"] not in MIX_NAMES:
                    raise McpError("function must be 'max' or 'average'")
                c["SelectedMixFunction"] = MIX_NAMES[a["function"]]
        if "hysteresis" in a:
            if kind not in ("graph", "linear"):
                raise McpError("hysteresis only applies to graph and linear curves")
            h = a["hysteresis"] or {}
            up, down, rtu, rtd, ign = hysteresis(c)
            set_hysteresis(c, _num(h.get("up", up), "up"), _num(h.get("down", down), "down"),
                           _num(h.get("response_up", rtu), "response_up"),
                           _num(h.get("response_down", rtd), "response_down"),
                           bool(h.get("ignore_at_limits", ign)), new_style=cfg.uses_new_hysteresis)
        unknown = set(a) - {"temperature_source", "points", "curves", "function", "hysteresis"} - set(mapping)
        if unknown:
            raise McpError(f"fields not valid for a {kind} curve: {', '.join(sorted(unknown))}")

    def t_create_curve(self, name, type, **a):
        if type not in ALLOWED_CURVES:
            raise McpError(f"type must be one of {', '.join(ALLOWED_CURVES)}")
        if not isinstance(name, str) or not name.strip():
            raise McpError("name must not be empty")
        name = name.strip()

        def fn(cfg):
            if cfg.curve(name):
                raise McpError(f"a curve named '{name}' already exists – use update_curve")
            if type != "mix" and "temperature_source" not in a:
                raise McpError("temperature_source is required")
            c = new_curve(type, name, cfg)
            if type == "graph":
                set_hysteresis(c, 2, 3, 1, 2, True, cfg.uses_new_hysteresis)
            elif type == "linear":
                c["MinimumFanSpeed"] = max(20.0, self.floor)
            elif type == "auto":
                c["MinFanSpeed"] = max(20.0, self.floor)
            cfg.curves.append(c)
            self._apply_curve_fields(cfg, c, a)
            if type == "graph" and "points" not in a:
                raise McpError("points are required for graph curves")
            self.check_curve(cfg, c)
            return {"created": name, "curve": copy.deepcopy(c)}
        return self._mutate(fn, "Laget kurven «{}»", name)

    def t_update_curve(self, name, new_name=None, **a):
        def fn(cfg):
            c = self._curve(cfg, name)
            self._apply_curve_fields(cfg, c, a)
            final = name
            if new_name and new_name != name:
                if cfg.curve(new_name):
                    raise McpError(f"a curve named '{new_name}' already exists")
                cfg.rename_curve(name, new_name)
                final = new_name
            self.check_curve(cfg, cfg.curve(final))
            # Kontroller som bruker kurven (direkte eller via mix) må fortsatt holde gulvet.
            for ctl in cfg.controls:
                ref = (ctl.get("SelectedFanCurve") or {}).get("Name")
                if ctl.get("Enable") and ref and self._depends_on(cfg, ref, final):
                    self.check_control(cfg, ctl)
            return {"updated": final, "curve": copy.deepcopy(cfg.curve(final))}
        return self._mutate(fn, "Endret kurven «{}»", new_name or name)

    def _depends_on(self, cfg, curve_name, target, depth=0):
        if curve_name == target:
            return True
        c = cfg.curve(curve_name)
        if not c or depth > 5:
            return False
        return any(self._depends_on(cfg, r.get("Name"), target, depth + 1) for r in c.get("SelectedFanCurves") or [])

    def t_delete_curve(self, name):
        def fn(cfg):
            self._curve(cfg, name)
            ctrls, mixes = self._users_of(cfg, name)
            if ctrls or mixes:
                users = [c.get("NickName") or c.get("Identifier") for c in ctrls] + [m.get("Name") for m in mixes]
                raise McpError(f"curve '{name}' is used by: {', '.join(users)}")
            cfg.delete_curve(name)
            return {"deleted": name}
        return self._mutate(fn, "Slettet kurven «{}»", name)

    # ------------------------------------------------------------------ kontroller
    def t_configure_control(self, control, **a):
        allowed = {"enabled", "curve", "manual", "offset", "minimum", "start", "stop", "step_up",
                   "step_down", "name"}
        unknown = set(a) - allowed
        if unknown:
            raise McpError(f"unknown fields: {', '.join(sorted(unknown))}")

        def fn(cfg):
            c = self._control_entry(cfg, control)
            if self.d.engine.resolve(c.get("Identifier")) is None:
                raise McpError("this control is not present on this machine")
            if "curve" in a:
                if a["curve"] in (None, ""):
                    c["SelectedFanCurve"] = None
                else:
                    self._curve(cfg, a["curve"])
                    c["SelectedFanCurve"] = cfg.make_curve_ref(a["curve"])
            if "manual" in a:
                if a["manual"] is None:
                    c["ManualControl"] = False
                else:
                    c["ManualControl"] = True
                    c["ManualControlValue"] = int(round(max(0.0, min(100.0, _num(a["manual"], "manual")))))
            for key, field in (("offset", "SelectedOffset"), ("minimum", "MinimumPercent"),
                               ("start", "SelectedStart"), ("stop", "SelectedStop"),
                               ("step_up", "SelectedCommandStepUp"), ("step_down", "SelectedCommandStepDown")):
                if key in a:
                    v = _num(a[key], key)
                    if key != "offset" and not 0 <= v <= 100:
                        raise McpError(f"{key} must be 0–100")
                    c[field] = int(round(v)) if key != "offset" else round(v, 1)
            if "name" in a and a["name"]:
                c["NickName"] = str(a["name"])
            if "enabled" in a:
                c["Enable"] = bool(a["enabled"])
            elif a.get("curve") or a.get("manual") is not None:
                c["Enable"] = True    # å velge kurve eller fast verdi betyr å ta styringen
            self.check_control(cfg, c)
            return {"control": c.get("Identifier"), "enabled": bool(c.get("Enable")),
                    "settings": {k: c.get(k) for k in ("SelectedFanCurve", "ManualControl", "ManualControlValue",
                                                       "SelectedOffset", "MinimumPercent", "SelectedStart",
                                                       "SelectedStop", "SelectedCommandStepUp",
                                                       "SelectedCommandStepDown")}}
        return self._mutate(fn, "Endret viften «{}»", control)

    # ------------------------------------------------------------------ sensorer
    def t_create_custom_sensor(self, name, type, sources, seconds=5):
        if type not in ("max", "average", "time_average"):
            raise McpError("type must be max, average or time_average")
        if not isinstance(sources, list) or not sources:
            raise McpError("sources must be a non-empty list of sensor ids")

        def fn(cfg):
            if any(cs.get("Name") == name for cs in cfg.custom_sensors):
                raise McpError(f"a custom sensor named '{name}' already exists")
            ids = [self._sensor_ref(cfg, s) for s in sources]
            if type == "time_average":
                if len(ids) != 1:
                    raise McpError("time_average takes exactly one source")
                secs = _num(seconds, "seconds")
                if not 1 <= secs <= MAX_TIME_AVERAGE:
                    raise McpError(f"seconds must be 1–{MAX_TIME_AVERAGE:g}")
                cs = new_custom_sensor("time", name)
                cs["SelectedTempSource"] = cfg.make_ref(ids[0], cfg.display_name(ids[0]))
                cs["SelectedTime"] = secs
            else:
                cs = new_custom_sensor("mix", name)
                cs["SelectedMixFunction"] = MIX_NAMES[type]
                cs["SelectedSensors"] = [cfg.make_ref(i, cfg.display_name(i)) for i in ids]
            cfg.custom_sensors.append(cs)
            self.check_source(cfg, cs["Identifier"])
            return {"created": cs["Identifier"]}
        return self._mutate(fn, "Laget sensoren «{}»", name)

    def t_rename_sensor(self, id, name):
        def fn(cfg):
            e = cfg.sensor_entry(id)
            if e is None or e in cfg.controls:
                raise McpError(f"no temperature or fan sensor '{id}'")
            e["NickName"] = str(name)
            return {"renamed": id, "name": name}
        return self._mutate(fn, "Ga «{}» nytt navn", name)

    # ------------------------------------------------------------------ oppgaver
    def t_pair_fans(self):
        self.d._start_task("pair", self.d.pair, self.floor, self.d.protected_for_claude())
        self.d.log_mcp("Startet paring av vifter")
        return {"started": True, "hint": "poll get_task every ~15 s"}

    def t_calibrate_control(self, control):
        c = self._control_entry(self.d.engine.cfg, control)
        local = self.d.engine.resolve(c.get("Identifier"))
        if local is not None and local.id in self.d.protected_for_claude():
            raise McpError("this control is a pump or locked by the user – Claude can't calibrate it")
        self.d._start_task("calibrate", self.d.calibrate, c.get("Identifier"), self.floor)
        self.d.log_mcp("Startet kalibrering av «{}»", c.get("NickName") or c.get("Identifier"))
        return {"started": True, "hint": "poll get_task every ~15 s"}

    def t_get_task(self):
        t = self.d.task
        if not t:
            return {"task": None}
        return {k: v for k, v in t.items() if k not in ("cancel", "msg")}

    # ------------------------------------------------------------------ konfigurasjoner
    def t_list_configurations(self):
        return {"active": self.d.settings["current"], "configurations": self.d.list_configs(),
                "unsaved_changes": self.d.dirty}

    def t_switch_configuration(self, name):
        from .daemon import safe_name
        if safe_name(name) not in self.d.list_configs():
            raise McpError(f"no configuration '{name}'")
        if self.d.dirty:
            raise McpError("the active configuration has unsaved changes – save or undo first")
        # Pumper følger maskinen og påvirkes ikke av konfigurasjonen; låste vifter må være like.
        target = FanControlConfig.load(self.d.path(name))
        self.d._add_hardware(target)
        with self.d.lock:
            self.check_protected(self.d.engine.cfg, target, include_pumps=False)
        self.d.load(name)
        self.d.log_mcp("Byttet til konfigurasjonen «{}»", name)
        return {"active": self.d.settings["current"]}

    def t_save_configuration(self, name=None):
        from .daemon import safe_name
        d = self.d
        if name and safe_name(name) != d.settings["current"] and safe_name(name) in d.list_configs():
            raise McpError(f"'{safe_name(name)}' already exists – choose a new name (existing "
                           "configurations are never overwritten)")
        with d.lock:
            self.backup(d.settings["current"])
            d.save(name)
        d.log_mcp("Lagret konfigurasjonen «{}»", d.settings["current"])
        return {"saved": d.settings["current"]}

    def backup(self, name):
        src = self.d.path(name)
        if not os.path.exists(src):
            return
        bdir = os.path.join(self.d.conf_dir, "backups")
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(bdir, f"{name[:-5]}-{stamp}.json")
        n = 1
        while os.path.exists(dst):
            n += 1
            dst = os.path.join(bdir, f"{name[:-5]}-{stamp}-{n}.json")
        with open(src, "rb") as f, open(dst, "wb") as g:
            g.write(f.read())
        files = sorted(os.listdir(bdir))
        for old in files[:-30]:
            os.remove(os.path.join(bdir, old))

    def t_undo(self):
        d = self.d
        with d.lock:
            if not d.undo_stack:
                raise McpError("nothing to undo")
            before, rev_after = d.undo_stack[-1]
            if rev_after != d.rev:
                d.undo_stack.clear()
                raise McpError("the configuration has been changed in the Vifte app since then – "
                               "undo is no longer possible")
            self.check_protected(d.engine.cfg, FanControlConfig(before), include_pumps=False)
            d.undo_stack.pop()
            d.commit_config(FanControlConfig(before))
            if d.undo_stack:
                prev_before, _ = d.undo_stack.pop()
                d.undo_stack.append((prev_before, d.rev))
            d.log_mcp("Angret siste endring")
        return {"undone": True, "remaining_undo_steps": len(d.undo_stack)}


def _tool(name, description, props, required=()):
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return {"name": name, "description": description, "inputSchema": schema}
