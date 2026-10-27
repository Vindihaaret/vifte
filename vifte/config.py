"""Lesing/skriving av FanControl-konfigurasjonsfiler (userConfig.json).

Filen holdes som den rå JSON-strukturen, slik at felter vi ikke kjenner til
bevares uendret når filen lagres igjen. Tre varianter støttes:

  * ny (v260+):    {"__VERSION__", "FanControl": {...}, "MainWindow": {...}, "Sensors": {...}}
  * mellomversjon: {"__VERSION__", "Main": {... + HysteresisConfig i kurvene}, "Sensors": {...}}
  * gammel:        {"Main": {...}} med SelectedHysteresis/OneWayHysteresis i kurvene
"""
import copy
import json

DEFAULT_VERSION = "277"

# Funksjoner for Mix-kurver og Mix-sensorer (SelectedMixFunction)
MIX_FUNCTIONS = ["Maks", "Min", "Gjennomsnitt", "Sum", "Differanse"]

CURVE_TYPES = ["graph", "linear", "flat", "mix", "trigger", "sync", "auto"]
CURVE_TITLES = {
    "graph": "Graf", "linear": "Lineær", "flat": "Flat", "mix": "Mix",
    "trigger": "Trigger", "sync": "Synk", "auto": "Auto", "unknown": "Ukjent",
}
SENSOR_TYPES = {"mix": "Mix", "time": "Tidsgjennomsnitt", "file": "Fil", "offset": "Forskyvning"}


def curve_type(c):
    if "Points" in c:
        return "graph"
    if "SelectedFanCurves" in c:
        return "mix"
    if "Percent" in c:
        return "flat"
    if "SelectedControl" in c:
        return "sync"
    if "Deadband" in c or "Step" in c:
        return "auto"
    if "IdleFanSpeed" in c:
        return "trigger"
    if "MinimumFanSpeed" in c:
        return "linear"
    return "unknown"


def custom_sensor_type(s):
    ident = s.get("Identifier", "")
    if "SelectedSensors" in s or ident.startswith("Mix/"):
        return "mix"
    if "SelectedTime" in s or ident.startswith("Time/"):
        return "time"
    if "FileName" in s or ident.startswith("File/"):
        return "file"
    return "offset"


def parse_point(p):
    if isinstance(p, str):
        t, v = p.split(",")
        return float(t), float(v)
    if isinstance(p, dict):
        return float(p.get("X", p.get("Temperature", 0))), float(p.get("Y", p.get("Value", 0)))
    return float(p[0]), float(p[1])


def calibration_points(cal):
    """[(prosent, rpm)] fra en kontrolls Calibration. FanControl på Windows skriver
    [prosent, rpm, false]; eldre Vifte-filer har [prosent, rpm]. Ugyldige punkter hoppes over."""
    out = []
    for item in cal or []:
        try:
            if isinstance(item, dict):
                out.append((float(item.get("Percent", item.get("X"))), float(item.get("Rpm", item.get("Y")))))
            else:
                out.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def calibration_entry(table):
    """{prosent: rpm} -> Calibration i samme form som FanControl på Windows."""
    return [[p, table[p], False] for p in sorted(table)]


def format_point(t, v):
    def f(x):
        x = round(float(x), 6)
        return str(int(x)) if x == int(x) else repr(x)
    return f"{f(t)},{f(v)}"


def hysteresis(c):
    """Returnerer (opp °C, ned °C, responstid opp s, responstid ned s, ignorer ved grenser)."""
    h = c.get("HysteresisConfig")
    if isinstance(h, dict):
        return (float(h.get("HysteresisValueUp", 2)), float(h.get("HysteresisValueDown", 2)),
                float(h.get("ResponseTimeUp", 1)), float(h.get("ResponseTimeDown", 1)),
                bool(h.get("IgnoreHysteresisAtLimits", True)))
    hyst = float(c.get("SelectedHysteresis", 2) or 0)
    rt = float(c.get("SelectedResponseTime", 1) or 0)
    up = 0.0 if c.get("OneWayHysteresis") else hyst
    return up, hyst, rt, rt, False


def set_hysteresis(c, up, down, rt_up, rt_down, ignore_limits, new_style=True):
    if "HysteresisConfig" in c or (new_style and "SelectedHysteresis" not in c):
        c["HysteresisConfig"] = {
            "ResponseTimeUp": rt_up, "ResponseTimeDown": rt_down,
            "HysteresisValueUp": up, "HysteresisValueDown": down,
            "IgnoreHysteresisAtLimits": bool(ignore_limits),
        }
    else:
        c["SelectedHysteresis"] = float(down)
        c["SelectedResponseTime"] = rt_down
        c["OneWayHysteresis"] = up == 0 and down > 0


def _whole_numbers_as_int(o):
    """FanControl (.NET/Newtonsoft) godtar ikke «120.0» i et heltallsfelt, men godtar «120»
    i et desimalfelt – så hele tall skrives alltid uten desimaler."""
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {k: _whole_numbers_as_int(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_whole_numbers_as_int(v) for v in o]
    return o


class FanControlConfig:
    def __init__(self, raw=None):
        if raw is None:
            raw = {
                "__VERSION__": DEFAULT_VERSION,
                "FanControl": {"Controls": [], "CustomSensors": [], "FanCurves": [],
                               "FanSensors": [], "TemperatureSensors": []},
                "MainWindow": {"Fahrenheit": False, "HideCalibration": False,
                               "HideFanSpeedCards": False, "HorizontalUIOrientation": False,
                               "PrimaryColor": "#FF607D8B", "SecondaryColor": "#FFC6FF00",
                               "SelectedTheme": "", "ShowHiddenCards": False,
                               "SyncThemeWithWindows": True, "SyncTrayIconColorWithWindows": True,
                               "TrayIconColor": None, "TrayIcons": []},
            }
        self.raw = raw
        main = self.main
        for k in ("Controls", "CustomSensors", "FanCurves", "FanSensors", "TemperatureSensors"):
            if main.get(k) is None:
                main[k] = []

    # --- inn/ut
    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8-sig") as f:
            return cls(json.load(f))

    @classmethod
    def loads(cls, text):
        return cls(json.loads(text.lstrip("﻿")))

    def dumps(self):
        if not self.is_new_format:
            self.sync_embedded()
        return json.dumps(_whole_numbers_as_int(self.raw), indent=2, ensure_ascii=False)

    def sync_embedded(self):
        """Eldre filer lagrer fulle kopier av kurvene i hver referanse; hold dem oppdatert."""
        def fresh(ref):
            c = self.curve((ref or {}).get("Name"))
            return copy.deepcopy(c) if c is not None and c is not ref else ref
        for _ in range(3):  # nok for mix-i-mix
            for c in self.curves:
                if "SelectedFanCurves" in c:
                    c["SelectedFanCurves"] = [fresh(r) for r in c["SelectedFanCurves"]]
        for ctl in self.controls:
            if ctl.get("SelectedFanCurve"):
                ctl["SelectedFanCurve"] = fresh(ctl["SelectedFanCurve"])

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.dumps())

    def copy(self):
        return FanControlConfig(copy.deepcopy(self.raw))

    # --- seksjoner
    @property
    def is_new_format(self):
        return "FanControl" in self.raw

    @property
    def main(self):
        if "FanControl" in self.raw:
            return self.raw["FanControl"]
        return self.raw.setdefault("Main", {})

    @property
    def window(self):
        if "MainWindow" in self.raw:
            return self.raw["MainWindow"]
        return self.main

    @property
    def uses_new_hysteresis(self):
        if self.is_new_format or "__VERSION__" not in self.raw:
            return self.is_new_format
        return any("HysteresisConfig" in c for c in self.curves) or int(
            str(self.raw.get("__VERSION__", "0")).split(".")[0] or 0) >= 200

    @property
    def controls(self):
        return self.main["Controls"]

    @property
    def curves(self):
        return self.main["FanCurves"]

    @property
    def custom_sensors(self):
        return self.main["CustomSensors"]

    @property
    def fan_sensors(self):
        return self.main["FanSensors"]

    @property
    def temp_sensors(self):
        return self.main["TemperatureSensors"]

    # --- oppslag
    def curve(self, name):
        for c in self.curves:
            if c.get("Name") == name:
                return c
        return None

    def control(self, ident):
        for c in self.controls:
            if c.get("Identifier") == ident:
                return c
        return None

    def resolve_curve(self, ref):
        """En kurvereferanse er {"Name": ...} (ny) eller en full kopi (gammel)."""
        if not ref:
            return None
        return self.curve(ref.get("Name")) or (ref if curve_type(ref) != "unknown" else None)

    def sensor_entry(self, ident):
        for lst in (self.temp_sensors, self.fan_sensors, self.controls):
            for s in lst:
                if s.get("Identifier") == ident:
                    return s
        return None

    def display_name(self, ident, default=None):
        e = self.sensor_entry(ident)
        if e:
            return e.get("NickName") or e.get("Name") or default or ident
        return default or ident

    # --- referanser i det formatet filen bruker
    def make_ref(self, ident, name=None):
        if self.is_new_format:
            return {"Identifier": ident}
        ref = {"Identifier": ident, "IsHidden": False}
        if name:
            ref["Name"] = name
            ref["NickName"] = self.display_name(ident, name)
        return ref

    def make_curve_ref(self, name):
        if self.is_new_format:
            return {"Name": name}
        c = self.curve(name)
        return copy.deepcopy(c) if c else {"Name": name}

    # --- oppdatering av kjente sensorer/kontroller
    def ensure_hardware(self, sensors, controls):
        """Legger til maskinvare som ikke står i filen ennå (som FanControl gjør)."""
        known = {s.get("Identifier") for s in self.temp_sensors + self.fan_sensors + self.controls}
        new = self.is_new_format
        changed = False
        for s in sensors:
            if s["id"] in known:
                continue
            entry = {"Identifier": s["id"], "IsHidden": False, "NickName": s["name"]}
            if not new:
                entry["Name"] = s["name"]
            (self.fan_sensors if s["kind"] == "fan" else self.temp_sensors).append(entry)
            changed = True
        for c in controls:
            if c["id"] in known:
                continue
            ctl = {"NickName": c["name"], "Identifier": c["id"], "IsHidden": False, "Enable": False,
                   "SelectedFanCurve": None, "SelectedOffset": 0, "PairedFanSensor": None,
                   "SelectedStart": 0, "SelectedStop": 0, "MinimumPercent": 0,
                   "SelectedCommandStepUp": 8, "SelectedCommandStepDown": 8,
                   "ManualControlValue": 50, "ManualControl": False, "ForceApply": False,
                   "Calibration": None}
            if not new:
                ctl["Name"] = c["name"]
            self.controls.append(ctl)
            changed = True
        return changed

    def rename_curve(self, old, new):
        def walk(o):
            if isinstance(o, dict):
                if o.get("Name") == old and ("SelectedFanCurves" in o or curve_type(o) != "unknown"
                                             or set(o.keys()) <= {"Name", "IsHidden"}):
                    o["Name"] = new
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(self.main)

    def delete_curve(self, name):
        self.main["FanCurves"] = [c for c in self.curves if c.get("Name") != name]
        for ctl in self.controls:
            if (ctl.get("SelectedFanCurve") or {}).get("Name") == name:
                ctl["SelectedFanCurve"] = None
        for c in self.curves:
            if "SelectedFanCurves" in c:
                c["SelectedFanCurves"] = [r for r in c["SelectedFanCurves"] if r.get("Name") != name]


def new_curve(kind, name, cfg, source=None):
    new_h = cfg.uses_new_hysteresis
    c = {"Name": name, "IsHidden": False}
    if cfg.is_new_format:
        c["CommandMode"] = 0
    src = cfg.make_ref(source) if source else None
    if kind == "graph":
        c.update({"SelectedTempSource": src, "Points": ["30,20", "50,40", "70,70", "85,100"],
                  "MaximumTemperature": 120, "MinimumTemperature": 20, "MaximumCommand": 100})
        set_hysteresis(c, 2, 2, 1, 1, True, new_h)
    elif kind == "linear":
        c.update({"SelectedTempSource": src, "MinimumTemperature": 40, "MaximumTemperature": 80,
                  "MinimumFanSpeed": 20, "MaximumFanSpeed": 100})
        set_hysteresis(c, 2, 2, 1, 1, True, new_h)
    elif kind == "flat":
        c["Percent"] = 50
    elif kind == "mix":
        c.update({"SelectedFanCurves": [], "SelectedMixFunction": 0})
    elif kind == "trigger":
        c.update({"SelectedTempSource": src, "IdleTemperature": 45, "LoadTemperature": 65,
                  "IdleFanSpeed": 30, "LoadFanSpeed": 70, "SelectedResponseTime": 5})
    elif kind == "sync":
        c.update({"SelectedControl": None, "SelectedOffset": 0, "Proportional": False})
    elif kind == "auto":
        c.update({"SelectedTempSource": src, "IdleTemperature": 45, "LoadTemperature": 70,
                  "MinFanSpeed": 20, "MaxFanSpeed": 100, "Step": 2, "Deadband": 2,
                  "SelectedResponseTime": 2})
    return c


def new_custom_sensor(kind, name):
    if kind == "mix":
        return {"Identifier": f"Mix/{name}", "Name": name, "SelectedMixFunction": 0, "SelectedSensors": []}
    if kind == "time":
        return {"Identifier": f"Time/{name}", "Name": name, "SelectedTempSource": None, "SelectedTime": 5}
    if kind == "file":
        return {"Identifier": f"File/{name}", "Name": name, "FileName": ""}
    return {"Identifier": f"Offset/{name}", "Name": name, "SelectedTempSource": None,
            "Offset": 0, "Proportional": False}
