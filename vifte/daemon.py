"""Bakgrunnstjenesten (kjører som root via systemd).

Styrer viftene uavhengig av om GUI-et er åpent, og snakker med GUI/CLI over en
Unix-socket med linjebasert JSON: {"cmd": "...", ...} -> {"ok": true, ...}
"""
import argparse
import copy
import grp
import json
import logging
import math
import os
import pwd
import re
import shutil
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
from collections import deque

from . import hw
from .config import FanControlConfig, calibration_entry, calibration_points
from .engine import Engine
from .mcpapi import McpApi

log = logging.getLogger("vifte")

SYSTEM_DIR = "/var/lib/vifte"
SYSTEM_SOCKET = "/run/vifte/vifte.sock"
MCP_DISABLED = ("Claude/MCP access is turned off in Vifte. Ask the user to enable it under "
                "Settings → Claude.")
NOT_IN_GROUP = "ingen tilgang: brukeren er ikke med i gruppen vifte (kjør install.sh på nytt)"


def user_paths():
    run = os.environ.get("XDG_RUNTIME_DIR", f"/tmp/vifte-{os.getuid()}")
    data = os.path.join(os.path.expanduser("~/.local/share"), "vifte-test")
    return data, os.path.join(run, "vifte.sock")


def safe_name(name):
    name = os.path.basename(name.strip())
    name = re.sub(r"[^\w .,()+-]", "_", name)
    if not name.lower().endswith(".json"):
        name += ".json"
    return name


def write_atomic(path, text, backup=False):
    """Skriver filen ferdig ved siden av og bytter den inn til slutt, så en feil underveis
    (full disk, strømbrudd) aldri etterlater en halv eller manglende fil. Med backup=True
    kopieres den forrige utgaven til «.bak» først."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if backup and os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except OSError as e:
            log.warning("kunne ikke ta sikkerhetskopi av %s: %s", path, e)
    os.replace(tmp, path)


def sd_notify(message):
    """Melding til systemd (vakthund). Gjør ingenting når tjenesten ikke er startet av systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode(), addr)
    except OSError:
        pass


class Daemon:
    HISTORY_EVERY = 5.0      # sekunder mellom hvert historikkpunkt
    TICK_FAILURES_BEFORE_RELEASE = 5   # så mange mislykkede runder på rad før BIOS får viftene
    HISTORY_KEEP = 360       # 30 minutter
    # Kommer ikke beskjeden om oppvåkning (vifte-resume.service), tar tjenesten viftene selv
    # etter så mange sekunder våken tid. time.monotonic() står stille mens maskinen sover.
    SLEEP_MAX_AWAKE = 120.0

    def __init__(self, data_dir, dry_run=False, engine=None):
        self.data_dir = data_dir
        self.conf_dir = os.path.join(data_dir, "Configurations")
        os.makedirs(self.conf_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, "sensors"), exist_ok=True)
        self.lock = threading.RLock()
        self.engine = engine or Engine(dry_run=dry_run)
        self.engine.sensor_dir = os.path.join(data_dir, "sensors")
        self.state_file = os.path.join(data_dir, "state.json")
        self.settings = {"current": "userConfig.json", "interval": 1.0, "aliases": {},
                         "mcp_enabled": False, "mcp_floor": 25, "failsafe_enabled": False,
                         "failsafe_temp": 95, "sensor_lost_enabled": True, "sensor_lost_speed": 80,
                         "pumps": {}, "claude_locked": []}
        defaults = dict(self.settings)
        try:
            with open(self.state_file) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.settings.update(loaded)
            else:
                log.error("%s er ugyldig, bruker standardinnstillinger", self.state_file)
        except (OSError, ValueError):
            pass
        # En ødelagt verdi i filen skal ikke hindre tjenesten i å starte.
        for key in ("interval", "mcp_floor", "failsafe_temp", "sensor_lost_speed"):
            try:
                if not math.isfinite(float(self.settings[key])):
                    raise ValueError(key)
            except (TypeError, ValueError):
                self.settings[key] = defaults[key]
        if not isinstance(self.settings.get("current"), str) or not self.settings["current"].strip():
            self.settings["current"] = defaults["current"]
        for key, kind in (("aliases", dict), ("pumps", dict), ("claude_locked", list)):
            if not isinstance(self.settings.get(key), kind):
                self.settings[key] = kind()
        hw.ModeStore.path = None if self.engine.dry_run else os.path.join(data_dir, "pwm_modes.json")
        self.engine.interval = float(self.settings.get("interval", 1.0))
        self.engine.aliases = dict(self.settings.get("aliases") or {})
        self.apply_safety()
        self.history = deque(maxlen=self.HISTORY_KEEP)
        self._last_history = 0.0
        self.mcp_log = deque(maxlen=50)
        self._mcp_log_id = 0
        self.undo_stack = deque(maxlen=20)
        self.mcp = McpApi(self)
        self.rev = 0
        self.dirty = False
        self.task = None   # {"kind", "progress", "message", "cancel"}
        self.stop = threading.Event()
        self.reapply = threading.Event()   # settes etter hvilemodus (SIGUSR1)
        self.sleep_request = threading.Event()   # settes før hvilemodus (SIGUSR2)
        self.sleeping = None   # time.monotonic() da maskinen skulle i hvilemodus, ellers None
        self._failures = 0
        try:
            self.load(self.settings["current"], create=True)
        except Exception as ex:
            self._recover(self.settings["current"], ex)

    # ------------------------------------------------------------ filer
    def path(self, name):
        return os.path.join(self.conf_dir, safe_name(name))

    def apply_safety(self):
        """Nødmodus og sensortap-sikring er alltid på når Claude (MCP) har tilgang."""
        st = self.settings
        mcp = bool(st.get("mcp_enabled"))
        st["mcp_floor"] = int(max(10, min(60, float(st.get("mcp_floor", 25)))))
        st["failsafe_temp"] = float(max(70, min(105, float(st.get("failsafe_temp", 95)))))
        st["sensor_lost_speed"] = int(max(30, min(100, float(st.get("sensor_lost_speed", 80)))))
        self.engine.failsafe_enabled = bool(st.get("failsafe_enabled")) or mcp
        self.engine.failsafe_temp = st["failsafe_temp"]
        self.engine.sensor_lost_enabled = bool(st.get("sensor_lost_enabled", True)) or mcp
        self.engine.sensor_lost_speed = float(max(st["sensor_lost_speed"], st["mcp_floor"] if mcp else 0))
        pumps = {}
        for ident, p in (st.get("pumps") or {}).items():
            if isinstance(p, dict) and p.get("mode") in ("bios", "fixed"):
                try:
                    speed = float(p.get("speed", 100))
                    speed = speed if math.isfinite(speed) else 100.0
                except (TypeError, ValueError):
                    speed = 100.0
                pumps[ident] = {"mode": p["mode"], "speed": int(max(Engine.PUMP_MIN, min(100, speed)))}
        st["pumps"] = pumps
        st["claude_locked"] = sorted({str(i) for i in st.get("claude_locked") or []})
        self.engine.pumps = pumps

    def local_control(self, ident):
        """Lokal id for en kontroll i konfigurasjonen (pumper og låser gjelder maskinen)."""
        c = self.engine.resolve(ident)
        if c is None or c.kind != "control":
            raise RuntimeError("fant ikke kontrollen på denne maskinen")
        return c.id

    def protected_for_claude(self):
        """Kontroller Claude ikke får endre: alle pumper og de brukeren har låst."""
        return set(self.settings.get("pumps") or {}) | set(self.settings.get("claude_locked") or [])

    def safety(self):
        st = self.settings
        return {"mcp_enabled": bool(st.get("mcp_enabled")), "floor": st["mcp_floor"],
                "failsafe_enabled": bool(st.get("failsafe_enabled")),
                "failsafe_effective": self.engine.failsafe_enabled, "failsafe_temp": st["failsafe_temp"],
                "sensor_lost_enabled": bool(st.get("sensor_lost_enabled", True)),
                "sensor_lost_effective": self.engine.sensor_lost_enabled,
                "sensor_lost_speed": st["sensor_lost_speed"],
                "pumps": st["pumps"], "claude_locked": st["claude_locked"]}

    def log_mcp(self, template, *args):
        self._mcp_log_id += 1
        self.mcp_log.append({"id": self._mcp_log_id, "ts": time.time(),
                             "msg": [template, [str(a) for a in args]], "text": template.format(*args)})
        log.info("Claude: %s", template.format(*args))

    def commit_config(self, cfg):
        """Bytter til en endret konfigurasjon (holdes på låsen av kalleren)."""
        e = self.engine
        e.cfg = cfg
        names = {c.get("Name") for c in cfg.curves}
        for n in list(e.curve_state):
            if n not in names:
                del e.curve_state[n]
        self.rev += 1
        self.dirty = True

    def record_history(self):
        now = time.time()
        if now - self._last_history < self.HISTORY_EVERY:
            return
        self._last_history = now
        e = self.engine
        temps = {s.id: round(s.value, 1) for s in e.failsafe_sensors() if s.value is not None}
        for c in e.cfg.curves:
            src = (c.get("SelectedTempSource") or {}).get("Identifier")
            v = e.temperature(src) if src else None
            if v is not None:
                temps[src] = round(v, 1)
        controls = {}
        for ident, st in e.control_state.items():
            if st.get("found"):
                controls[ident] = [st.get("out"), None if st.get("rpm") is None else round(st["rpm"])]
        # Alle sensorer (for grafene i appen); "temps" over er det utvalget Claude får.
        sensors = {s.id: round(s.value, 1) for s in e.sensors
                   if s.value is not None and not (isinstance(s.value, float) and s.value != s.value)}
        sensors.update({k: round(v, 1) for k, v in e.custom_values.items() if v is not None})
        self.history.append({"t": round(now), "temps": temps, "controls": controls, "sensors": sensors})

    def save_settings(self):
        with self.lock:
            self.settings["aliases"] = self.engine.aliases
            self.settings["interval"] = self.engine.interval
            write_atomic(self.state_file, json.dumps(self.settings, indent=2))

    def list_configs(self):
        # En ny konfigurasjon som ikke er lagret ennå, finnes bare i minnet, men vises likevel.
        files = {f for f in os.listdir(self.conf_dir) if f.lower().endswith(".json")}
        return sorted(files | {self.settings["current"]})

    def _add_hardware(self, cfg):
        """Ny maskinvare føres inn i konfigurasjonen, men ikke maskinvare som allerede
        er knyttet til en (omkoblet) oppføring – da ville den dukket opp to ganger."""
        e = self.engine
        old = e.cfg
        e.cfg = cfg
        try:
            used = set()
            for entry in cfg.temp_sensors + cfg.fan_sensors + cfg.controls:
                s = e.resolve(entry.get("Identifier"))
                if s:
                    used.add(s.id)
        finally:
            e.cfg = old
        sensors = [{"id": s.id, "name": s.name, "kind": s.kind} for s in e.sensors if s.id not in used]
        controls = [{"id": c.id, "name": c.name} for c in e.controls if c.id not in used]
        return cfg.ensure_hardware(sensors, controls)

    def load(self, name, create=False):
        name = safe_name(name)
        p = self.path(name)
        with self.lock:   # hele byttet under låsen, så reguleringen aldri ser en halvferdig tilstand
            if os.path.exists(p):
                cfg = FanControlConfig.load(p)
            elif create:
                cfg = FanControlConfig()
            else:
                raise FileNotFoundError(name)
            changed = self._add_hardware(cfg)
            self._activate(cfg, name)
            if not os.path.exists(p):
                write_atomic(p, cfg.dumps())
            elif changed:
                self.dirty = True
            self.save_settings()
        log.info("konfigurasjon lastet: %s", name)

    def _recover(self, name, ex):
        """Den aktive konfigurasjonen lar seg ikke lese ved oppstart. Legg den til side og bruk
        sikkerhetskopien, ellers en tom konfigurasjon: tjenesten må starte, ellers kan ikke
        appen brukes til å rette opp noe."""
        p = self.path(name)
        log.error("kan ikke lese %s (%s)", p, ex)
        if os.path.exists(p):
            os.replace(p, p + ".corrupt")
            log.error("den ødelagte filen er lagt til side som %s.corrupt", p)
        bak = p + ".bak"
        if os.path.exists(bak):
            try:
                shutil.copyfile(bak, p)
                self.load(name)
                log.warning("bruker sikkerhetskopien %s", bak)
                return
            except Exception as ex2:
                log.error("sikkerhetskopien kan heller ikke leses (%s)", ex2)
                if os.path.exists(p):
                    os.remove(p)
        self.load(name, create=True)

    def import_name(self, name, text):
        """Navnet en importert fil lagres under. En konfigurasjon som finnes fra før blir aldri
        overskrevet: er innholdet et annet, får den nye et ledig navn («navn (2).json»)."""
        stem, n, cand = name[:-5], 1, name
        while cand in self.list_configs():
            try:
                if FanControlConfig.load(self.path(cand)).dumps() == text:
                    return cand
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            n += 1
            cand = f"{stem} ({n}).json"
        return cand

    def new(self, name):
        """Ny tom konfigurasjon. Den skrives ikke til disk før den lagres, så «Forkast» (bytte
        til en annen uten å lagre) fjerner den helt."""
        name = safe_name(name)
        if os.path.exists(self.path(name)):
            raise RuntimeError("det finnes allerede en konfigurasjon med det navnet")
        cfg = FanControlConfig()
        with self.lock:
            self._add_hardware(cfg)
            self._activate(cfg, name)
            self.dirty = True
            self.save_settings()
        log.info("ny konfigurasjon (ikke lagret): %s", name)

    def _activate(self, cfg, name):
        with self.lock:
            self.engine.release_all()
            self.undo_stack.clear()
            self.engine.cfg = cfg
            self.engine.curve_state.clear()
            self.engine.control_state.clear()
            self.settings["current"] = name
            self.rev += 1
            self.dirty = False

    def save(self, name=None):
        name = safe_name(name or self.settings["current"])
        with self.lock:
            write_atomic(self.path(name), self.engine.cfg.dumps(), backup=True)
            self.settings["current"] = name
            self.dirty = False
            self.save_settings()

    # ------------------------------------------------------------ hovedløkke
    def step(self):
        """Én runde i reguleringen. Feiler den flere ganger på rad, gis viftene tilbake til
        BIOS/driveren i stedet for å bli stående på den siste verdien."""
        with self.lock:
            try:
                if self.sleep_request.is_set():
                    self.sleep_request.clear()
                    self.reapply.clear()
                    self.prepare_sleep()
                if self.reapply.is_set():
                    self.reapply.clear()
                    if self.sleeping is not None:
                        self.sleeping = None
                        log.info("maskinen er våken igjen, tar viftene tilbake")
                    else:
                        log.info("tar viftene på nytt (etter hvilemodus)")
                    self.engine.reapply()
                if self.sleeping is not None:
                    if time.monotonic() - self.sleeping < self.SLEEP_MAX_AWAKE:
                        return
                    log.warning("fikk ingen beskjed om oppvåkning, tar viftene tilbake likevel")
                    self.sleeping = None
                self.engine.tick()
                self.record_history()
                self._failures = 0
            except Exception:
                log.exception("feil i reguleringsløkken")
                self._failures += 1
                if self._failures == self.TICK_FAILURES_BEFORE_RELEASE:
                    log.error("reguleringen feiler gjentatte ganger – viftene gis tilbake til BIOS/driver")
                if self._failures >= self.TICK_FAILURES_BEFORE_RELEASE:
                    self.engine.release_all()

    def prepare_sleep(self):
        """Før hvilemodus (kalleren holder låsen): gi viftene tilbake til BIOS/driveren og
        les ikke maskinvaren igjen før beskjeden om oppvåkning. Leser tjenesten skjermkortet
        (NVML) mens NVIDIA-driveren ennå holder på å våkne, kan driveren låse seg, og da
        blir skjermen stående svart."""
        if self.task and not self.task.get("done"):
            self.task["cancel"] = True
        self.engine.release_all()
        self.sleeping = time.monotonic()
        log.info("hvilemodus: viftene er gitt tilbake til BIOS/driver til maskinen våkner")

    def run(self):
        sd_notify("READY=1")
        while not self.stop.is_set():
            start = time.monotonic()
            self.step()
            sd_notify("WATCHDOG=1")   # uteblir den (reguleringen henger), starter systemd tjenesten på nytt
            self.stop.wait(max(0.05, self.engine.interval - (time.monotonic() - start)))
        with self.lock:
            self.engine.release_all()
        log.info("avsluttet, viftene er gitt tilbake til BIOS/driver")

    # ------------------------------------------------------------ bakgrunnsjobber
    def _start_task(self, kind, fn, *args, undo=False):
        """undo=True (jobber Claude starter): resultatet blir et eget angre-trinn."""
        with self.lock:
            if self.task and not self.task.get("done"):
                raise RuntimeError("en annen jobb kjører allerede")
            if self.sleeping is not None:
                raise RuntimeError("maskinen går i hvilemodus – prøv igjen når den er våken")
            self.task = {"kind": kind, "progress": 0.0, "done": False, "cancel": False, "result": None,
                         "undo": undo}
            self._say(self.task, "Starter …")

        def runner():
            try:
                self.task["result"] = fn(self.task, *args)
                self._say(self.task, "Ferdig")
            except _Cancelled:
                self._say(self.task, "Avbrutt")
            except Exception as e:
                log.exception("jobb feilet")
                self._say(self.task, "Feil: {}", e)
            finally:
                with self.lock:
                    self.engine.override.clear()
                self.task["done"] = True
                self.task["progress"] = 1.0
        threading.Thread(target=runner, daemon=True).start()

    @staticmethod
    def _say(task, template, *args):
        """Meldingen sendes både ferdig formatert (norsk) og som mal + argumenter,
        slik at GUI-et kan vise den på valgt språk."""
        task["message"] = template.format(*args)
        task["msg"] = [template, [str(a) for a in args]]

    def _task_commit(self, task, change):
        """Fører resultatet av en jobb inn i konfigurasjonen (kalleren holder låsen). For jobber
        Claude startet legges forrige tilstand på angre-stakken, så «angre» fortsatt virker."""
        before = copy.deepcopy(self.engine.cfg.raw) if task.get("undo") else None
        rev_before = self.rev
        change()
        self.rev += 1
        self.dirty = True
        if before is not None and (not self.undo_stack or self.undo_stack[-1][1] == rev_before):
            self.undo_stack.append((before, self.rev))

    def _commit_control(self, task, ident, change):
        """Som _task_commit, for én kontroll. Oppføringen slås opp på nytt: konfigurasjonen kan
        være byttet ut (endring i appen eller fra Claude) mens jobben gikk."""
        with self.lock:
            entry = self.engine.cfg.control(ident)
            if entry is None:
                raise RuntimeError("konfigurasjonen ble byttet under kalibreringen")
            self._task_commit(task, lambda: change(entry))

    def _wait(self, task, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if task["cancel"] or self.stop.is_set():
                raise _Cancelled()
            time.sleep(0.2)

    def _rpm(self, sensor, task, samples=3):
        vals = []
        for _ in range(samples):
            self._wait(task, 0.5)
            v = sensor.update()
            if v is not None:
                vals.append(v)
        return sum(vals) / len(vals) if vals else 0.0

    def calibrate(self, task, ident, min_percent=0):
        """Kalibrering. Med min_percent > 0 (brukes av Claude) går viften aldri under den
        grensen, så stopp/start-søket hoppes over."""
        e = self.engine
        entry = e.cfg.control(ident) or {}
        ctl = e.resolve(ident)
        paired = (entry.get("PairedFanSensor") or entry.get("MatchingFanSensor") or {}).get("Identifier")
        fan = e.resolve(paired) if paired else None
        if not ctl or not fan:
            raise RuntimeError("kontrollen må være paret med en hastighetssensor først")
        if ctl.id in e.pumps:
            raise RuntimeError("en pumpe kan ikke kalibreres")
        steps = [p for p in (100, 90, 80, 70, 60, 50, 40, 30, 20, 10) if p >= min_percent]
        if min_percent and min_percent not in steps:
            steps.append(min_percent)
        table = {}

        def setp(p):
            with self.lock:
                e.override[ctl.id] = p

        self._say(task, "Kjører viften på 100 % …")
        setp(100)
        self._wait(task, 6)
        for i, p in enumerate(steps):
            setp(p)
            self._say(task, "Måler {} % …", p)
            self._wait(task, 4)
            table[p] = round(self._rpm(fan, task))
            task["progress"] = 0.6 * (i + 1) / len(steps)
        if not any(table[p] > 0 for p in steps):
            # Uten turtall ville stopp-søket endt på 100 % og slått viften av.
            raise RuntimeError("viften ga ikke turtall under kalibreringen – sjekk at den er paret "
                               "med riktig hastighetssensor")
        if min_percent > 0:
            def store_points(entry):
                old = {int(p): r for p, r in calibration_points(entry.get("Calibration")) if p < min_percent}
                table.update(old)
                entry["Calibration"] = calibration_entry(table)
            self._commit_control(task, ident, store_points)
            return {"points": len(table)}
        # Finn stopp-punkt: gå ned fra laveste punkt som fortsatt spinner.
        spinning = [p for p in steps if table[p] > 0]
        low = min(spinning) if spinning else 100
        stop = low
        p = low
        while p > 0:
            p -= 2
            setp(max(p, 0))
            self._wait(task, 3)
            r = self._rpm(fan, task, 2)
            if r <= 0:
                break
            stop = p
            table.setdefault(max(p, 1), round(r))
        self._say(task, "Måler stopp …")
        setp(0)
        self._wait(task, 8)
        table[0] = round(self._rpm(fan, task))
        task["progress"] = 0.8
        # Finn start-punkt: øk fra 0 til viften starter.
        start = stop
        if table[0] <= 0:
            p = 0
            while p < 100:
                p += 2
                setp(p)
                self._say(task, "Finner startpunkt ({} %) …", p)
                self._wait(task, 3)
                if self._rpm(fan, task, 2) > 0:
                    start = p
                    break
                setp(0)
                self._wait(task, 1)
            task["progress"] = 0.95
        else:
            stop = start = 0
        def store(entry):
            entry["Calibration"] = calibration_entry(table)
            if not entry.get("SelectedStart") and not entry.get("SelectedStop"):
                entry["SelectedStop"] = stop
                entry["SelectedStart"] = max(start, stop)
        self._commit_control(task, ident, store)
        return {"start": start, "stop": stop}

    def pair(self, task, min_percent=0, exclude=()):
        """Pumper (og det som står i exclude) holdes helt utenfor – de settes aldri ned."""
        e = self.engine
        level = max(40, min_percent)
        fans = [s for s in e.sensors if s.kind == "fan"]
        controls = [c for c in e.controls if c.id not in e.pumps and c.id not in exclude]
        result = {}
        # Skjermkort: vifte N hører alltid til utgang N, så de trenger ikke kjøres opp.
        for c in list(controls):
            m = re.fullmatch(r"(NVApiWrapper/[^/]+)/control/(\d+)", c.id)
            fan = e.by_id.get(f"{m.group(1)}/fan/{m.group(2)}") if m else None
            if fan is not None and fan.kind == "fan":
                result[c.id] = fan.id
                controls.remove(c)
        self._say(task, "Setter alle vifter til {} % …", level)
        with self.lock:
            for c in controls:
                e.override[c.id] = level
        self._wait(task, 8)
        for i, c in enumerate(controls):
            self._say(task, "Tester {} …", c.name)
            base = {f.id: self._rpm(f, task, 2) for f in fans}
            with self.lock:
                e.override[c.id] = 100
            self._wait(task, 6)
            after = {f.id: self._rpm(f, task, 2) for f in fans}
            with self.lock:
                e.override[c.id] = level
            best, delta = None, 0
            for f in fans:
                d = after[f.id] - base[f.id]
                if d > delta:
                    best, delta = f.id, d
            if best and delta > 150 and delta > 0.2 * max(base[best], 1):
                result[c.id] = best
            task["progress"] = (i + 1) / len(controls)
            self._wait(task, 5)
        def store():
            cfg = e.cfg
            for entry in cfg.controls:
                local = e.resolve(entry.get("Identifier"))
                if local and local.id in result:
                    key = "MatchingFanSensor" if "MatchingFanSensor" in entry else "PairedFanSensor"
                    fan_id = result[local.id]
                    # bruk filens egen identifikator for viften hvis den finnes
                    for fs in cfg.fan_sensors:
                        if e.resolve(fs.get("Identifier")) and e.resolve(fs.get("Identifier")).id == fan_id:
                            fan_id = fs["Identifier"]
                            break
                    entry[key] = cfg.make_ref(fan_id, e.by_id[result[local.id]].name)
        with self.lock:
            self._task_commit(task, store)
        return result

    # ------------------------------------------------------------ kommandoer
    def handle(self, req):
        cmd = req.get("cmd")
        e = self.engine
        if cmd == "state":
            with self.lock:
                snap = e.snapshot()
            task = None
            if self.task:
                task = {k: v for k, v in self.task.items() if k not in ("cancel", "undo")}
            snap.update(rev=self.rev, dirty=self.dirty, current=self.settings["current"],
                        configs=self.list_configs(), task=task, interval=e.interval,
                        safety=self.safety(), mcp_log=list(self.mcp_log)[-10:],
                        sleeping=self.sleeping is not None)
            return snap
        if cmd == "get_config":
            with self.lock:
                return {"name": self.settings["current"], "raw": e.cfg.raw, "rev": self.rev,
                        "dirty": self.dirty}
        if cmd == "apply_config":
            cfg = FanControlConfig(req["raw"])
            with self.lock:
                # Appen sender hele konfigurasjonen; bygger den på en eldre utgave, ville den
                # overskrevet det Claude eller en kalibrering har endret i mellomtiden.
                if req.get("rev") is not None and req["rev"] != self.rev:
                    raise RuntimeError("konfigurasjonen er endret et annet sted – prøv igjen")
                self.commit_config(cfg)
                if req.get("save"):
                    self.save(req.get("name"))
            return {"rev": self.rev}
        if cmd == "save":
            with self.lock:
                self.save(req.get("name"))
            return {"rev": self.rev}
        if cmd == "load":
            self.load(req["name"])
            return {"rev": self.rev}
        if cmd == "new":
            self.new(req["name"])
            return {"rev": self.rev, "name": self.settings["current"]}
        if cmd == "import":
            name = safe_name(req["name"])
            cfg = FanControlConfig.loads(req["text"]) if "text" in req else FanControlConfig(req.get("raw"))
            text = cfg.dumps()
            with self.lock:
                name = self.import_name(name, text)
                write_atomic(self.path(name), text)
                if req.get("activate", True):
                    self.load(name)
            return {"name": name}
        if cmd == "export":
            name = safe_name(req.get("name") or self.settings["current"])
            if name == self.settings["current"]:
                with self.lock:
                    return {"text": e.cfg.dumps()}
            return {"text": FanControlConfig.load(self.path(name)).dumps()}
        if cmd == "delete":
            name = safe_name(req["name"])
            if name == self.settings["current"]:
                raise RuntimeError("kan ikke slette aktiv konfigurasjon")
            os.remove(self.path(name))
            return {}
        if cmd == "set_alias":
            with self.lock:
                if req.get("local"):
                    e.aliases[req["config_id"]] = req["local"]
                else:
                    e.aliases.pop(req["config_id"], None)
            self.save_settings()
            return {}
        if cmd == "settings":
            with self.lock:
                if "interval" in req:
                    e.interval = max(0.25, min(10.0, float(req["interval"])))
                if "paused" in req:
                    e.paused = bool(req["paused"])
                for key in ("mcp_enabled", "failsafe_enabled", "sensor_lost_enabled"):
                    if key in req:
                        self.settings[key] = bool(req[key])
                for key in ("mcp_floor", "failsafe_temp", "sensor_lost_speed"):
                    if key in req:
                        self.settings[key] = float(req[key])
                self.apply_safety()
            self.save_settings()
            return {"safety": self.safety()}
        if cmd == "history":
            with self.lock:
                rows = list(self.history)
            ids = req.get("ids")
            series = {}
            for i, r in enumerate(rows):
                for k, v in (r.get("sensors") or {}).items():
                    if ids is None or k in ids:
                        series.setdefault(k, [None] * len(rows))[i] = v
            return {"t": [r["t"] for r in rows], "series": series}
        if cmd == "set_pump":
            with self.lock:
                lid = self.local_control(req["id"])
                pumps = dict(self.settings.get("pumps") or {})
                if req.get("mode") in ("bios", "fixed"):
                    pumps[lid] = {"mode": req["mode"], "speed": req.get("speed", (pumps.get(lid) or {}).get("speed", 100))}
                else:
                    pumps.pop(lid, None)
                self.settings["pumps"] = pumps
                self.apply_safety()
            self.save_settings()
            return {"safety": self.safety()}
        if cmd == "set_claude_lock":
            with self.lock:
                lid = self.local_control(req["id"])
                locked = set(self.settings.get("claude_locked") or [])
                (locked.add if req.get("locked") else locked.discard)(lid)
                self.settings["claude_locked"] = sorted(locked)
                self.apply_safety()
            self.save_settings()
            return {"safety": self.safety()}
        if cmd == "mcp_tools":
            return {"tools": self.mcp.tools()}
        if cmd == "mcp_call":
            if not self.settings.get("mcp_enabled"):
                raise RuntimeError(MCP_DISABLED)
            return {"result": self.mcp.call(req.get("name"), req.get("args") or {})}
        if cmd == "calibrate":
            self._start_task("calibrate", self.calibrate, req["id"])
            return {}
        if cmd == "pair":
            self._start_task("pair", self.pair)
            return {}
        if cmd == "cancel":
            if self.task:
                self.task["cancel"] = True
            return {}
        if cmd == "rescan":
            with self.lock:
                e.rescan()
                self._add_hardware(e.cfg)
                self.rev += 1
            return {}
        raise RuntimeError(f"ukjent kommando: {cmd}")


class _Cancelled(Exception):
    pass


def peer_allowed(sock):
    """Tilgang: root, eller medlem av gruppen «vifte» slik den står i /etc/group nå.
    Sjekkes med SO_PEERCRED i stedet for filrettigheter, så en bruker som nettopp er
    lagt i gruppen slipper å logge ut og inn igjen."""
    pid, uid, gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                        struct.calcsize("3i")))
    if uid == 0:
        return True, uid
    try:
        group = grp.getgrnam("vifte")
    except KeyError:
        return False, uid
    try:
        user = pwd.getpwuid(uid)
    except KeyError:
        return False, uid
    return user.pw_name in group.gr_mem or user.pw_gid == group.gr_gid, uid


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        if self.server.check_peer:
            ok, uid = peer_allowed(self.request)
            if not ok:
                log.warning("avviste tilkobling fra uid %d (ikke i gruppen vifte)", uid)
                self.wfile.write((json.dumps({"ok": False, "error": NOT_IN_GROUP}) + "\n").encode())
                return
        for line in self.rfile:
            try:
                req = json.loads(line)
                resp = {"ok": True, **(self.server.daemon_obj.handle(req) or {})}
            except Exception as ex:
                log.debug("kommando feilet", exc_info=True)
                resp = {"ok": False, "error": str(ex)}
            self.wfile.write((json.dumps(resp) + "\n").encode())
            self.wfile.flush()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    check_peer = False


def wait_for_sleep(sock_path, timeout=10.0):
    """Venter til tjenesten melder at den har gitt fra seg viftene (etter SIGUSR2). Hvilemodus
    skal aldri stoppe av dette: kjører ikke tjenesten, eller svarer den ikke, gis det opp."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(sock_path)
                s.sendall(b'{"cmd": "state"}\n')
                with s.makefile("rb") as f:
                    resp = json.loads(f.readline() or b"{}")
            if resp.get("sleeping"):
                return True
        except (OSError, ValueError):
            if not os.path.exists(sock_path):
                return False
        time.sleep(0.2)
    log.warning("vifted meldte ikke at viftene er gitt fra seg før hvilemodus")
    return False


def main():
    ap = argparse.ArgumentParser(prog="vifted", description="Vifte – vifte-styringstjeneste")
    ap.add_argument("--user-test", action="store_true",
                    help="kjør som vanlig bruker med egen socket og tørrkjøring (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="ikke skriv til maskinvaren")
    ap.add_argument("-c", "--config", help="last denne konfigurasjonen ved oppstart")
    ap.add_argument("--release-fans", action="store_true",
                    help="gi tilbake vifter som står igjen i manuell modus etter et avbrudd, og avslutt")
    ap.add_argument("--wait-for-sleep", action="store_true",
                    help="vent til tjenesten har gitt fra seg viftene før hvilemodus (vifte-suspend.service)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    # Til de virkelige håndtererne er på plass under: et signal skal ikke avslutte tjenesten.
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.release_fans:
        hw.ModeStore.path = os.path.join(SYSTEM_DIR, "pwm_modes.json")
        n = hw.restore_saved_modes()
        if n:
            log.warning("%d vifte(r) sto igjen i manuell modus og er gitt tilbake til BIOS", n)
        return
    if a.wait_for_sleep:
        wait_for_sleep(SYSTEM_SOCKET)
        return
    if a.user_test:
        data_dir, sock = user_paths()
        dry = True
    else:
        if os.geteuid() != 0 and not a.dry_run:
            sys.exit("vifted må kjøre som root (eller bruk --user-test)")
        data_dir, sock = SYSTEM_DIR, SYSTEM_SOCKET
        dry = a.dry_run
    d = Daemon(data_dir, dry_run=dry)
    if a.config:
        if os.path.isfile(a.config):
            name = safe_name(os.path.basename(a.config))
            with open(a.config, encoding="utf-8-sig") as f:
                d.handle({"cmd": "import", "name": name, "text": f.read()})
        else:
            d.load(a.config)

    os.makedirs(os.path.dirname(sock), exist_ok=True)
    if os.path.exists(sock):
        os.remove(sock)
    srv = Server(sock, Handler)
    srv.daemon_obj = d
    if not a.user_test:
        # Alle kan koble til socketen, men tjenesten slipper bare inn root og
        # medlemmer av gruppen «vifte» (se peer_allowed).
        os.chmod(sock, 0o666)
        srv.check_peer = True
        try:
            grp.getgrnam("vifte")
        except KeyError:
            log.warning("gruppen «vifte» finnes ikke; bare root kan bruke GUI-et")
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def stop(*_):
        d.stop.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # «systemctl reload vifted» (etter hvilemodus): ta viftene på nytt uten å starte tjenesten
    # på nytt, så ulagrede endringer ikke går tapt.
    signal.signal(signal.SIGUSR1, lambda *_: d.reapply.set())
    # vifte-suspend.service før hvilemodus: gi fra seg viftene og hold seg unna maskinvaren
    signal.signal(signal.SIGUSR2, lambda *_: d.sleep_request.set())
    log.info("vifted kjører (socket %s%s)", sock, ", TØRRKJØRING" if dry else "")
    d.run()
    srv.shutdown()
    try:
        os.remove(sock)
    except OSError:
        pass


if __name__ == "__main__":
    main()
