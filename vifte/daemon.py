"""Bakgrunnstjenesten (kjører som root via systemd).

Styrer viftene uavhengig av om GUI-et er åpent, og snakker med GUI/CLI over en
Unix-socket med linjebasert JSON: {"cmd": "...", ...} -> {"ok": true, ...}
"""
import argparse
import grp
import json
import logging
import os
import pwd
import re
import signal
import socket
import socketserver
import struct
import sys
import threading
import time
from collections import deque

from .config import FanControlConfig
from .engine import Engine
from .mcpapi import McpApi

log = logging.getLogger("vifte")

SYSTEM_DIR = "/var/lib/vifte"
SYSTEM_SOCKET = "/run/vifte/vifte.sock"
MCP_DISABLED = ("Claude/MCP access is turned off in Vifte. Ask the user to enable it under "
                "Settings → Claude (MCP).")
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


class Daemon:
    HISTORY_EVERY = 5.0      # sekunder mellom hvert historikkpunkt
    HISTORY_KEEP = 360       # 30 minutter

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
                         "failsafe_temp": 95, "sensor_lost_enabled": True, "sensor_lost_speed": 80}
        try:
            with open(self.state_file) as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
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
        self.load(self.settings["current"], create=True)

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

    def safety(self):
        st = self.settings
        return {"mcp_enabled": bool(st.get("mcp_enabled")), "floor": st["mcp_floor"],
                "failsafe_enabled": bool(st.get("failsafe_enabled")),
                "failsafe_effective": self.engine.failsafe_enabled, "failsafe_temp": st["failsafe_temp"],
                "sensor_lost_enabled": bool(st.get("sensor_lost_enabled", True)),
                "sensor_lost_effective": self.engine.sensor_lost_enabled,
                "sensor_lost_speed": st["sensor_lost_speed"]}

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
        self.settings["aliases"] = self.engine.aliases
        self.settings["interval"] = self.engine.interval
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.settings, f, indent=2)
        os.replace(tmp, self.state_file)

    def list_configs(self):
        return sorted(f for f in os.listdir(self.conf_dir) if f.lower().endswith(".json"))

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
        if os.path.exists(p):
            cfg = FanControlConfig.load(p)
        elif create:
            cfg = FanControlConfig()
        else:
            raise FileNotFoundError(name)
        changed = self._add_hardware(cfg)
        with self.lock:
            self.engine.release_all()
            self.undo_stack.clear()
            self.engine.cfg = cfg
            self.engine.curve_state.clear()
            self.engine.control_state.clear()
            self.settings["current"] = name
            self.rev += 1
            self.dirty = False
            if not os.path.exists(p):
                cfg.save(p)
            elif changed:
                self.dirty = True
        self.save_settings()
        log.info("konfigurasjon lastet: %s", name)

    def save(self, name=None):
        name = safe_name(name or self.settings["current"])
        p = self.path(name)
        if os.path.exists(p):
            try:
                os.replace(p, p + ".bak")
            except OSError:
                pass
        tmp = p + ".tmp"
        self.engine.cfg.save(tmp)
        os.replace(tmp, p)
        self.settings["current"] = name
        self.dirty = False
        self.save_settings()

    # ------------------------------------------------------------ hovedløkke
    def run(self):
        while not self.stop.is_set():
            start = time.monotonic()
            with self.lock:
                try:
                    self.engine.tick()
                    self.record_history()
                except Exception:
                    log.exception("feil i reguleringsløkken")
            self.stop.wait(max(0.05, self.engine.interval - (time.monotonic() - start)))
        with self.lock:
            self.engine.release_all()
        log.info("avsluttet, viftene er gitt tilbake til BIOS/driver")

    # ------------------------------------------------------------ bakgrunnsjobber
    def _start_task(self, kind, fn, *args):
        if self.task and not self.task.get("done"):
            raise RuntimeError("en annen jobb kjører allerede")
        self.task = {"kind": kind, "progress": 0.0, "done": False, "cancel": False, "result": None}
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
        entry = e.cfg.control(ident)
        ctl = e.resolve(ident)
        paired = (entry.get("PairedFanSensor") or entry.get("MatchingFanSensor") or {}).get("Identifier")
        fan = e.resolve(paired) if paired else None
        if not ctl or not fan:
            raise RuntimeError("kontrollen må være paret med en hastighetssensor først")
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
        if min_percent > 0:
            with self.lock:
                old = {int(p): r for p, r in (entry.get("Calibration") or []) if p < min_percent}
                table.update(old)
                entry["Calibration"] = [[p, table[p]] for p in sorted(table)]
                self.rev += 1
                self.dirty = True
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
        with self.lock:
            entry["Calibration"] = [[p, table[p]] for p in sorted(table)]
            if not entry.get("SelectedStart") and not entry.get("SelectedStop"):
                entry["SelectedStop"] = stop
                entry["SelectedStart"] = max(start, stop)
            self.rev += 1
            self.dirty = True
        return {"start": start, "stop": stop}

    def pair(self, task, min_percent=0):
        e = self.engine
        level = max(40, min_percent)
        fans = [s for s in e.sensors if s.kind == "fan"]
        controls = list(e.controls)
        result = {}
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
        with self.lock:
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
            self.rev += 1
            self.dirty = True
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
                task = {k: v for k, v in self.task.items() if k != "cancel"}
            snap.update(rev=self.rev, dirty=self.dirty, current=self.settings["current"],
                        configs=self.list_configs(), task=task, interval=e.interval,
                        safety=self.safety(), mcp_log=list(self.mcp_log)[-10:])
            return snap
        if cmd == "get_config":
            with self.lock:
                return {"name": self.settings["current"], "raw": e.cfg.raw, "rev": self.rev,
                        "dirty": self.dirty}
        if cmd == "apply_config":
            cfg = FanControlConfig(req["raw"])
            with self.lock:
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
        if cmd == "import":
            name = safe_name(req["name"])
            cfg = FanControlConfig.loads(req["text"]) if "text" in req else FanControlConfig(req.get("raw"))
            cfg.save(self.path(name))
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


def main():
    ap = argparse.ArgumentParser(prog="vifted", description="Vifte – vifte-styringstjeneste")
    ap.add_argument("--user-test", action="store_true",
                    help="kjør som vanlig bruker med egen socket og tørrkjøring (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="ikke skriv til maskinvaren")
    ap.add_argument("-c", "--config", help="last denne konfigurasjonen ved oppstart")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    log.info("vifted kjører (socket %s%s)", sock, ", TØRRKJØRING" if dry else "")
    d.run()
    srv.shutdown()
    try:
        os.remove(sock)
    except OSError:
        pass


if __name__ == "__main__":
    main()
