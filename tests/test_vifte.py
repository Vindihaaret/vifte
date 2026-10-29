"""Tester for Vifte med simulert maskinvare (ingen ekte vifter berøres).

Kjør fra prosjektmappen:  python3 -m unittest discover -s tests -v
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vifte import hw  # noqa: E402
from vifte.config import FanControlConfig, new_curve  # noqa: E402
from vifte.daemon import Daemon  # noqa: E402
from vifte.engine import Engine, Hysteresis  # noqa: E402
from vifte.mcp import Server  # noqa: E402
from vifte.mcpapi import McpError  # noqa: E402

CPU = "/amdcpu/0/temperature/2"
GPU = "NVApiWrapper/0-AD103-A/sensor/0"
CASE = "/lpc/nct6798d/temperature/1"
FAN0 = "/lpc/nct6798d/control/0"
FAN1 = "/lpc/nct6798d/control/1"


class FakeBackend:
    def __init__(self):
        self.value = 0.0
        self.writes = []
        self.taken = False

    def read(self):
        return self.value

    def is_manual(self):
        return True

    def take(self):
        self.taken = True

    def write(self, p):
        self.value = p
        self.writes.append(p)

    def release(self):
        self.taken = False


class FakeHardware:
    def __init__(self):
        self.temps = {CPU: 45.0, GPU: 40.0, CASE: 30.0}
        self.rpm = {"/lpc/nct6798d/fan/0": 800.0, "/lpc/nct6798d/fan/1": 900.0}
        self.backends = {FAN0: FakeBackend(), FAN1: FakeBackend()}

    def build(self):
        sensors = [hw.Sensor(i, n, hw.TEMP, h, (lambda i=i: self.temps[i]))
                   for i, n, h in ((CPU, "Core (Tctl/Tdie)", "AMD CPU"), (GPU, "GPU", "NVIDIA"),
                                   (CASE, "SYSTIN", "Motherboard"))]
        sensors += [hw.Sensor(i, f"Fan #{k + 1}", hw.FAN, "Motherboard", (lambda i=i: self.rpm[i]))
                    for k, i in enumerate(self.rpm)]
        controls = [hw.Control(i, f"Fan #{k + 1}", "Motherboard", b)
                    for k, (i, b) in enumerate(self.backends.items())]
        return sensors, controls


def make_engine(fake=None):
    fake = fake or FakeHardware()
    return Engine(dry_run=True, hardware=fake.build()), fake


# ====================================================================== konfigurasjon

class ConfigTests(unittest.TestCase):
    NEW = {"__VERSION__": "277",
           "FanControl": {"Controls": [{"Identifier": FAN0, "Enable": True, "SelectedFanCurve": {"Name": "CPU"},
                                        "UnknownFutureField": [1, 2, 3]}],
                          "CustomSensors": [], "FanSensors": [], "TemperatureSensors": [],
                          "FanCurves": [{"Name": "CPU", "CommandMode": 0, "Points": ["40,30", "80,100"],
                                         "SelectedTempSource": {"Identifier": CPU},
                                         "HysteresisConfig": {"HysteresisValueUp": 2, "HysteresisValueDown": 2,
                                                              "ResponseTimeUp": 1, "ResponseTimeDown": 1,
                                                              "IgnoreHysteresisAtLimits": True}}]},
           "MainWindow": {"TrayIcons": [], "SomethingNew": True}}

    def test_round_trip_keeps_unknown_fields(self):
        cfg = FanControlConfig.loads(json.dumps(self.NEW))
        self.assertEqual(json.loads(cfg.dumps()), self.NEW)

    def test_whole_numbers_written_as_integers(self):
        cfg = FanControlConfig(json.loads(json.dumps(self.NEW)))
        cfg.curve("CPU")["MaximumTemperature"] = 120.0
        cfg.curve("CPU")["Fraction"] = 2.5
        text = cfg.dumps()
        self.assertIn('"MaximumTemperature": 120,', text)
        self.assertIn('"Fraction": 2.5', text)

    def test_old_format_embedded_curves_are_refreshed(self):
        old = {"Main": {"Controls": [{"Identifier": FAN0, "SelectedFanCurve": {"Name": "A", "Percent": 10}}],
                        "FanCurves": [{"Name": "A", "Percent": 10}]}}
        cfg = FanControlConfig(old)
        cfg.curve("A")["Percent"] = 55
        out = json.loads(cfg.dumps())
        self.assertEqual(out["Main"]["Controls"][0]["SelectedFanCurve"]["Percent"], 55)

    def test_rename_curve_updates_references(self):
        cfg = FanControlConfig(json.loads(json.dumps(self.NEW)))
        cfg.rename_curve("CPU", "Prosessor")
        self.assertEqual(cfg.controls[0]["SelectedFanCurve"]["Name"], "Prosessor")
        self.assertIsNotNone(cfg.curve("Prosessor"))


class CalibrationFormatTests(unittest.TestCase):
    WINDOWS = [[10, 0, False], [20, 340, False], [50, 775, False], [100, 1337, False]]

    def test_reads_windows_and_old_vifte_format(self):
        from vifte.config import calibration_points
        self.assertEqual(calibration_points(self.WINDOWS)[1], (20.0, 340.0))
        self.assertEqual(calibration_points([[20, 340], "bad", None, [5]]), [(20.0, 340.0)])
        self.assertEqual(calibration_points(None), [])

    def test_rpm_mode_with_windows_calibration(self):
        self.assertAlmostEqual(Engine.rpm_to_percent(self.WINDOWS, 775), 50)

    def test_written_like_fancontrol_on_windows(self):
        from vifte.config import calibration_entry
        self.assertEqual(calibration_entry({50: 775, 20: 340}), [[20, 340, False], [50, 775, False]])

    def test_focus_is_not_grabbed_in_a_loop(self):
        """GLib.idle_add(entry.grab_focus) gjentas så lenge grab_focus returnerer True, og markerer
        teksten på nytt hele tiden – da kan man bare skrive én bokstav."""
        import re
        gui = os.path.join(os.path.dirname(__file__), "..", "vifte", "gui")
        for name in os.listdir(gui):
            if name.endswith(".py"):
                with open(os.path.join(gui, name), encoding="utf-8") as f:
                    self.assertIsNone(re.search(r"idle_add\(\s*[\w.]+\.grab_focus\s*\)", f.read()), name)


# ====================================================================== motor

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.e, self.fake = make_engine()
        cfg = FanControlConfig()
        cfg.ensure_hardware([{"id": s.id, "name": s.name, "kind": s.kind} for s in self.e.sensors],
                            [{"id": c.id, "name": c.name} for c in self.e.controls])
        g = new_curve("graph", "CPU", cfg, CPU)
        g["Points"] = ["40,30", "60,60", "80,100"]
        cfg.curves.append(g)
        cfg.control(FAN0).update(Enable=True, SelectedFanCurve={"Name": "CPU"},
                                 SelectedCommandStepUp=100, SelectedCommandStepDown=100)
        self.e.cfg = cfg

    def test_graph_curve_interpolates(self):
        self.fake.temps[CPU] = 50
        self.e.tick()
        self.assertAlmostEqual(self.e.control_state[FAN0]["out"], 45.0)
        self.assertAlmostEqual(self.fake.backends[FAN0].value, 45.0)

    def test_disabled_control_is_not_touched(self):
        self.e.tick()
        self.assertEqual(self.fake.backends[FAN1].writes, [])

    def test_shape_stop_start_and_steps(self):
        c = {"SelectedStop": 20, "SelectedStart": 35, "SelectedCommandStepUp": 8,
             "SelectedCommandStepDown": 8, "MinimumPercent": 0}
        seq, last = [], 0
        for tgt in (0, 25, 25, 25, 60, 60, 10, 10, 10, 10, 10):
            last = Engine.shape(c, tgt, last)
            seq.append(last)
        self.assertEqual(seq, [0, 35, 27, 25, 33, 41, 33, 25, 0, 0, 0])

    def test_minimum_wins_over_stop(self):
        c = {"SelectedStop": 30, "MinimumPercent": 25}
        self.assertEqual(Engine.shape(c, 10, None), 25)

    def test_hysteresis_waits_for_response_time(self):
        h = Hysteresis()
        out = [h.filter(t, 2, 2, 2, 2, False, 20, 100, now)
               for now, t in enumerate((40, 41, 43, 43, 43, 39, 38, 37))]
        self.assertEqual(out, [40, 40, 40, 40, 43, 43, 43, 37])

    def test_failsafe_forces_100_and_releases_with_hysteresis(self):
        self.e.failsafe_enabled = True
        self.e.failsafe_temp = 90
        self.fake.temps[CPU] = 50
        self.e.tick()
        self.assertLess(self.fake.backends[FAN0].value, 100)
        self.fake.temps[GPU] = 91
        self.e.tick()
        self.assertTrue(self.e.failsafe_active)
        self.assertEqual(self.fake.backends[FAN0].value, 100)
        self.assertEqual(self.fake.backends[FAN1].writes, [], "disabled controls stay with the BIOS")
        self.fake.temps[GPU] = 86            # under 90, men over 90-7: fortsatt nødmodus
        self.e.tick()
        self.assertTrue(self.e.failsafe_active)
        self.fake.temps[GPU] = 80
        self.e.tick()
        self.assertFalse(self.e.failsafe_active)
        self.assertLess(self.fake.backends[FAN0].value, 100)

    def test_auto_curve(self):
        c = new_curve("auto", "A", self.e.cfg, CPU)
        c.update(IdleTemperature=40, LoadTemperature=70, MinFanSpeed=30, MaxFanSpeed=100, Step=2,
                 Deadband=2, SelectedResponseTime=1)
        from vifte.engine import CurveState
        st = CurveState()
        self.assertEqual(self.e._auto(c, st, 30, 0), 30)          # under tomgang
        self.assertAlmostEqual(self.e._auto(c, st, 55, 1), 65)    # lineært under lastsonen
        st = CurveState()
        self.e._auto(c, st, 69, 0)                               # inn i lastsonen
        self.assertGreaterEqual(self.e._auto(c, st, 80, 1), 100)  # plutselig varme: aldri under vernet

    def test_failsafe_ignores_non_core_sensors(self):
        self.e.failsafe_enabled = True
        self.e.failsafe_temp = 90
        self.fake.temps[CASE] = 99
        self.e.tick()
        self.assertFalse(self.e.failsafe_active)


class SensorLostTests(unittest.TestCase):
    """Sikring ved sensortap: viftene går på valgt hastighet når kurvens sensor forsvinner."""

    def setUp(self):
        EngineTests.setUp(self)
        self.e.sensor_lost_speed = 80
        self.clock = [100.0]
        patcher = mock.patch("vifte.engine.time.monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def tick(self, seconds=1):
        self.clock[0] += seconds
        self.e.tick()
        return self.fake.backends[FAN0].value

    def test_lost_sensor_runs_fans_at_chosen_speed_and_recovers(self):
        self.fake.temps[CPU] = 50
        self.assertAlmostEqual(self.tick(), 45)
        del self.fake.temps[CPU]                       # sensoren slutter å svare
        self.assertAlmostEqual(self.tick(), 45, msg="kort utfall: hold siste verdi")
        self.assertAlmostEqual(self.tick(5), 80)
        self.assertEqual(self.e.control_state[FAN0]["lost"], [CPU])
        self.assertEqual(self.e.snapshot()["sensor_lost"]["controls"], {FAN0: [CPU]})
        self.fake.temps[CPU] = 50                      # tilbake
        self.assertAlmostEqual(self.tick(), 45)
        self.assertIsNone(self.e.control_state[FAN0]["lost"])
        self.assertEqual(self.e.snapshot()["sensor_lost"]["controls"], {})

    def test_disabled_protection_holds_last_value(self):
        self.e.sensor_lost_enabled = False
        self.fake.temps[CPU] = 50
        self.tick()
        del self.fake.temps[CPU]
        self.assertAlmostEqual(self.tick(30), 45)
        self.assertIsNone(self.e.control_state[FAN0].get("lost"))

    def test_sensor_that_never_worked_leaves_fan_to_bios(self):
        del self.fake.temps[CPU]
        self.tick()
        self.tick(30)
        self.assertEqual(self.fake.backends[FAN0].writes, [])

    def test_mix_sensor_is_lost_when_one_source_is_missing(self):
        from vifte.config import new_custom_sensor
        mixs = new_custom_sensor("mix", "Maks")
        mixs.update(SelectedMixFunction=0, SelectedSensors=[{"Identifier": CPU}, {"Identifier": GPU}])
        self.e.cfg.custom_sensors.append(mixs)
        self.e.cfg.curve("CPU")["SelectedTempSource"] = {"Identifier": mixs["Identifier"]}
        self.fake.temps.update({CPU: 50, GPU: 40})
        self.assertAlmostEqual(self.tick(), 45)
        del self.fake.temps[GPU]                       # CPU svarer fortsatt, men GPU kan være varmest
        self.tick()
        self.assertAlmostEqual(self.tick(5), 80)
        self.assertEqual(self.e.control_state[FAN0]["lost"], [GPU])

    def test_emergency_mode_wins_over_sensor_loss(self):
        self.e.failsafe_enabled, self.e.failsafe_temp = True, 90
        self.fake.temps[CPU] = 50
        self.tick()
        del self.fake.temps[CPU]
        self.fake.temps[GPU] = 95
        self.assertEqual(self.tick(10), 100)


class PumpEngineTests(unittest.TestCase):
    def setUp(self):
        EngineTests.setUp(self)
        self.fake.temps[CPU] = 50

    def test_bios_pump_is_never_touched_even_if_the_configuration_enables_it(self):
        self.e.pumps = {FAN0: {"mode": "bios", "speed": 100}}
        self.e.failsafe_enabled, self.e.failsafe_temp = True, 90
        self.fake.temps[GPU] = 95                       # nødmodus
        self.e.tick()
        self.assertEqual(self.fake.backends[FAN0].writes, [])
        self.assertEqual(self.e.control_state[FAN0]["pump"], "bios")

    def test_marking_a_running_fan_as_pump_hands_it_back(self):
        self.e.tick()
        self.assertTrue(self.e.controls[0].taken)
        self.e.pumps = {FAN0: {"mode": "bios"}}
        self.e.tick()
        self.assertFalse(self.e.controls[0].taken)

    def test_fixed_pump_never_below_70_and_100_in_emergency(self):
        self.e.pumps = {FAN0: {"mode": "fixed", "speed": 20}}
        self.e.tick()
        self.assertEqual(self.fake.backends[FAN0].value, 70)
        self.e.failsafe_enabled, self.e.failsafe_temp = True, 90
        self.fake.temps[GPU] = 95
        self.e.tick()
        self.assertEqual(self.fake.backends[FAN0].value, 100)


# ====================================================================== MCP / sikkerhet

class McpTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.engine, self.fake = make_engine()
        self.d = Daemon(self.dir, dry_run=True, engine=self.engine)
        self.d.handle({"cmd": "settings", "mcp_enabled": True, "mcp_floor": 25, "failsafe_temp": 90})
        self.d.engine.tick()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def call(self, tool, **args):
        return self.d.handle({"cmd": "mcp_call", "name": tool, "args": args})["result"]

    def rejected(self, tool, **args):
        with self.assertRaises(McpError) as cm:
            self.call(tool, **args)
        return str(cm.exception)

    def good_curve(self, name="CPU"):
        return self.call("create_curve", name=name, type="graph", temperature_source=CPU,
                         points=[[40, 30], [60, 55], [85, 100]])

    def test_disabled_rejects_everything(self):
        self.d.handle({"cmd": "settings", "mcp_enabled": False})
        with self.assertRaises(RuntimeError) as cm:
            self.call("get_status")
        self.assertIn("turned off", str(cm.exception))

    def test_enabling_mcp_forces_failsafe(self):
        self.assertTrue(self.d.engine.failsafe_enabled)
        self.d.handle({"cmd": "settings", "mcp_enabled": False})
        self.assertFalse(self.d.engine.failsafe_enabled)

    def test_enabling_mcp_forces_sensor_loss_protection_above_floor(self):
        self.d.handle({"cmd": "settings", "sensor_lost_enabled": False, "sensor_lost_speed": 5,
                       "mcp_floor": 40})
        safety = self.d.safety()
        self.assertEqual(safety["sensor_lost_speed"], 30, "clamped to 30–100")
        self.assertTrue(self.d.engine.sensor_lost_enabled)
        self.assertEqual(self.d.engine.sensor_lost_speed, 40, "never below Claude's floor")
        self.d.handle({"cmd": "settings", "mcp_enabled": False})
        self.assertFalse(self.d.engine.sensor_lost_enabled)

    def test_new_configuration_is_only_written_when_saved(self):
        start = self.d.settings["current"]
        self.d.handle({"cmd": "new", "name": "Test"})
        self.assertEqual(self.d.settings["current"], "Test.json")
        self.assertFalse(os.path.exists(self.d.path("Test")))
        self.assertIn("Test.json", self.d.handle({"cmd": "state"})["configs"])
        self.d.handle({"cmd": "load", "name": start})            # «Forkast»: bytt uten å lagre
        self.assertNotIn("Test.json", self.d.handle({"cmd": "state"})["configs"])
        self.d.handle({"cmd": "new", "name": "Test"})
        self.d.handle({"cmd": "save"})
        self.assertTrue(os.path.exists(self.d.path("Test")))
        with self.assertRaises(RuntimeError):
            self.d.handle({"cmd": "new", "name": "Test"})

    def test_history_command_for_the_app(self):
        self.d._last_history = 0
        self.d.record_history()
        h = self.d.handle({"cmd": "history"})
        self.assertEqual(len(h["t"]), 1)
        self.assertEqual(h["series"][CASE], [30.0], "all sensors, not just CPU/GPU")
        self.assertEqual(set(self.d.handle({"cmd": "history", "ids": [CPU]})["series"]), {CPU})

    def test_status_lists_hardware(self):
        st = self.call("get_status")
        self.assertEqual({c["id"] for c in st["controls"]}, {FAN0, FAN1})
        self.assertEqual(st["safety"]["floor_percent"], 25)

    def test_curve_below_floor_rejected(self):
        msg = self.rejected("create_curve", name="X", type="graph", temperature_source=CPU,
                            points=[[30, 0], [80, 100]])
        self.assertIn("floor", msg)

    def test_falling_curve_rejected(self):
        self.assertIn("never fall", self.rejected("create_curve", name="X", type="graph", temperature_source=CPU,
                                                  points=[[30, 50], [60, 30], [80, 100]]))

    def test_curve_must_reach_100_before_emergency(self):
        self.assertIn("emergency", self.rejected("create_curve", name="X", type="graph",
                                                 temperature_source=CPU, points=[[30, 30], [95, 100]]))
        self.assertIn("never reaches 100", self.rejected("create_curve", name="Y", type="graph",
                                                         temperature_source=CPU, points=[[30, 30], [80, 90]]))

    def test_unknown_sensor_rejected(self):
        self.rejected("create_curve", name="X", type="graph", temperature_source="/nope/0",
                      points=[[30, 30], [80, 100]])

    def test_slow_hysteresis_rejected(self):
        self.assertIn("too slowly", self.rejected(
            "create_curve", name="X", type="graph", temperature_source=CPU, points=[[30, 30], [80, 100]],
            hysteresis={"up": 10}))

    def test_flat_and_other_types_rejected(self):
        self.rejected("create_curve", name="X", type="flat")
        cfg = self.d.engine.cfg.copy()
        flat = new_curve("flat", "Silent", cfg)
        flat["Percent"] = 10
        cfg.curves.append(flat)
        self.d.handle({"cmd": "apply_config", "raw": cfg.raw})
        self.assertIn("flat", self.rejected("configure_control", control=FAN0, curve="Silent"))

    def test_assign_valid_curve_and_engine_follows(self):
        self.good_curve()
        r = self.call("configure_control", control=FAN0, curve="CPU")
        self.assertTrue(r["enabled"])
        self.fake.temps[CPU] = 40
        for _ in range(20):
            self.d.engine.tick()
        self.assertAlmostEqual(self.fake.backends[FAN0].value, 30, delta=0.5)

    def test_control_rules(self):
        self.good_curve()
        self.assertIn("stop", self.rejected("configure_control", control=FAN0, curve="CPU", stop=40))
        self.assertIn("floor", self.rejected("configure_control", control=FAN0, curve="CPU", offset=-10,
                                             minimum=0))
        self.assertIn("offset", self.rejected("configure_control", control=FAN0, curve="CPU", offset=-30))
        self.assertIn("floor", self.rejected("configure_control", control=FAN0, manual=10))
        self.assertIn("step_up", self.rejected("configure_control", control=FAN0, curve="CPU", step_up=1))
        # minimum kan løfte en lav kurve over gulvet
        self.call("configure_control", control=FAN0, curve="CPU", offset=-10, minimum=25)

    def test_rejected_change_leaves_config_untouched(self):
        before = json.dumps(self.d.engine.cfg.raw, sort_keys=True)
        rev = self.d.rev
        self.rejected("configure_control", control=FAN0, manual=5)
        self.assertEqual(json.dumps(self.d.engine.cfg.raw, sort_keys=True), before)
        self.assertEqual(self.d.rev, rev)

    def test_release_to_bios_always_allowed(self):
        cfg = self.d.engine.cfg.copy()
        cfg.control(FAN0).update(Enable=True, ManualControl=True, ManualControlValue=0)
        self.d.handle({"cmd": "apply_config", "raw": cfg.raw})
        self.call("configure_control", control=FAN0, enabled=False)
        self.assertFalse(self.d.engine.cfg.control(FAN0)["Enable"])

    def test_editing_curve_in_use_is_checked_against_controls(self):
        self.good_curve()
        self.call("configure_control", control=FAN0, curve="CPU", offset=-5)
        self.assertIn("floor", self.rejected("update_curve", name="CPU", points=[[40, 25], [85, 100]]))

    def test_mix_rules(self):
        self.good_curve("A")
        self.call("create_curve", name="G", type="graph", temperature_source=GPU, points=[[40, 30], [80, 100]])
        self.call("create_curve", name="M", type="mix", curves=["A", "G"], function="max")
        self.call("configure_control", control=FAN1, curve="M")
        cfg = self.d.engine.cfg.copy()
        cfg.curve("M")["SelectedMixFunction"] = 1   # min – laget av brukeren
        self.d.handle({"cmd": "apply_config", "raw": cfg.raw})
        self.assertIn("max or average", self.rejected("configure_control", control=FAN1, curve="M"))

    def test_custom_sensor_rules(self):
        self.call("create_custom_sensor", name="Hottest", type="max", sources=[CPU, GPU])
        self.call("create_curve", name="Case", type="linear", temperature_source="Hottest",
                  min_temp=40, max_temp=80, min_speed=30)
        self.rejected("create_custom_sensor", name="Slow", type="time_average", sources=[CPU], seconds=120)

    def test_undo_and_conflict_with_app(self):
        self.good_curve()
        self.call("undo")
        self.assertIsNone(self.d.engine.cfg.curve("CPU"))
        self.good_curve()
        self.d.handle({"cmd": "apply_config", "raw": self.d.engine.cfg.raw})   # brukeren endrer i appen
        self.assertIn("changed in the Vifte app", self.rejected("undo"))

    def test_save_never_overwrites_other_configuration(self):
        with open(os.path.join(self.dir, "Configurations", "Gaming.json"), "w") as f:
            f.write("{}")
        self.assertIn("already exists", self.rejected("save_configuration", name="Gaming"))

    def test_tools_listed_even_when_service_is_down(self):
        class Down:
            def call(self, *a, **k):
                raise OSError("no socket")
        out = io.StringIO()
        Server(client=Down(), out=out).run(io.StringIO(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"))
        self.assertTrue(json.loads(out.getvalue())["result"]["tools"])

    def test_save_makes_backup(self):
        self.call("save_configuration")
        self.good_curve()
        self.call("save_configuration")
        backups = os.listdir(os.path.join(self.dir, "Configurations", "backups"))
        self.assertEqual(len(backups), 2)

    def test_calibration_never_goes_below_floor(self):
        task = {"cancel": False}
        self.d._wait = lambda t, s: None
        self.d._rpm = lambda s, t, n=3: 500.0
        seen = []
        orig = self.d.engine.override.__setitem__

        class Spy(dict):
            def __setitem__(s, k, v):
                seen.append(v)
                dict.__setitem__(s, k, v)
        self.d.engine.override = Spy()
        cfg = self.d.engine.cfg.copy()
        cfg.control(FAN0)["PairedFanSensor"] = {"Identifier": "/lpc/nct6798d/fan/0"}
        self.d.handle({"cmd": "apply_config", "raw": cfg.raw})
        self.d.calibrate(task, FAN0, min_percent=25)
        self.assertTrue(seen)
        self.assertGreaterEqual(min(seen), 25)
        del orig

    def test_pump_setting_is_stored_and_clamped(self):
        self.d.handle({"cmd": "set_pump", "id": FAN1, "mode": "fixed", "speed": 10})
        self.assertEqual(self.d.engine.pumps[FAN1], {"mode": "fixed", "speed": 70})
        d2 = Daemon(self.dir, dry_run=True, engine=make_engine()[0])
        self.assertEqual(d2.engine.pumps[FAN1]["mode"], "fixed", "survives a restart")
        self.d.handle({"cmd": "set_pump", "id": FAN1, "mode": None})
        self.assertEqual(self.d.engine.pumps, {})

    def test_pump_is_never_calibrated_or_paired(self):
        self.d.handle({"cmd": "set_pump", "id": FAN1, "mode": "bios"})
        self.d._wait = lambda t, s: None
        self.d._rpm = lambda s, t, n=3: 500.0
        cfg = self.d.engine.cfg.copy()
        cfg.control(FAN1)["PairedFanSensor"] = {"Identifier": "/lpc/nct6798d/fan/1"}
        self.d.handle({"cmd": "apply_config", "raw": cfg.raw})
        with self.assertRaises(RuntimeError):
            self.d.calibrate({"cancel": False}, FAN1)
        touched = set()

        class Spy(dict):
            def __setitem__(s, k, v):
                touched.add(k)
                dict.__setitem__(s, k, v)
        self.d.engine.override = Spy()
        self.d.pair({"cancel": False})
        self.assertIn(FAN0, touched)
        self.assertNotIn(FAN1, touched)

    def test_claude_cannot_change_a_pump(self):
        self.good_curve()
        self.d.handle({"cmd": "set_pump", "id": FAN1, "mode": "bios"})
        msg = self.rejected("configure_control", control=FAN1, curve="CPU")
        self.assertIn("pump", msg)
        self.rejected("configure_control", control=FAN1, name="Pump?")
        self.rejected("calibrate_control", control=FAN1)
        status = {c["id"]: c for c in self.call("get_status")["controls"]}
        self.assertEqual(status[FAN1]["pump"], "bios")
        self.assertFalse(status[FAN1]["claude_can_change"])
        self.assertTrue(status[FAN0]["claude_can_change"])

    def test_locked_fan_and_the_curves_it_uses_are_off_limits(self):
        self.good_curve("CPU")
        self.good_curve("Other")
        self.call("create_curve", name="Mix", type="mix", curves=["CPU", "Other"], function="max")
        self.call("configure_control", control=FAN0, curve="Mix")
        self.d.handle({"cmd": "set_claude_lock", "id": FAN0, "locked": True})
        self.assertIn("locked", self.rejected("configure_control", control=FAN0, offset=5))
        # indirekte: CPU-kurven inngår i mix-kurven den låste viften bruker
        self.rejected("update_curve", name="CPU", points=[[40, 35], [60, 60], [85, 100]])
        self.rejected("update_curve", name="Mix", function="average")
        # en kurve den låste viften ikke bruker, kan fortsatt endres
        self.call("create_curve", name="Free", type="graph", temperature_source=CPU,
                  points=[[40, 30], [60, 55], [85, 100]])
        self.call("update_curve", name="Free", points=[[40, 35], [60, 55], [85, 100]])
        self.call("configure_control", control=FAN1, curve="Free")
        self.d.handle({"cmd": "set_claude_lock", "id": FAN0, "locked": False})
        self.call("configure_control", control=FAN0, offset=5)

    def test_mcp_cannot_reach_dangerous_commands(self):
        for name in ("delete", "uninstall", "settings", "apply_config", "import", "pause"):
            self.rejected(name)

    def test_stdio_server_round_trip(self):
        d = self.d

        class Direct:
            def call(self, cmd, **kw):
                resp = d.handle(dict(cmd=cmd, **kw))
                return resp

        out = io.StringIO()
        srv = Server(client=Direct(), out=out)
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "configure_control", "arguments": {"control": FAN0, "manual": 0}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        ]
        srv.run(io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"))
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "vifte")
        self.assertIn("get_status", [t["name"] for t in replies[1]["result"]["tools"]])
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertIn("floor", replies[3]["result"]["content"][0]["text"])
        self.assertEqual(replies[4]["error"]["code"], -32601)

    def test_tool_schemas_are_valid_json_schema_objects(self):
        for t in self.d.mcp.tools():
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])
            for req in t["inputSchema"].get("required", []):
                self.assertIn(req, t["inputSchema"]["properties"])


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def make_tar(self, files, name="src.tar.gz"):
        import tarfile
        path = os.path.join(self.dir, name)
        with tarfile.open(path, "w:gz") as tar:
            for arc, data in files.items():
                info = tarfile.TarInfo(arc)
                raw = data.encode()
                info.size = len(raw)
                tar.addfile(info, io.BytesIO(raw))
        return path

    def test_versions(self):
        from vifte import updater
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2"), (1, 2, 0))
        self.assertIsNone(updater.parse_version("latest"))
        self.assertTrue(updater.is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(updater.is_newer("v1.0.1", "1.0.1"))
        self.assertFalse(updater.is_newer("v0.9.0", "1.0.0"))
        self.assertFalse(updater.is_newer("nonsense", "1.0.0"))

    def test_monthly_schedule(self):
        from vifte import updater
        now = 1_800_000_000
        self.assertTrue(updater.due(None, now))
        self.assertFalse(updater.due(now - 29 * 86400, now))
        self.assertTrue(updater.due(now - 31 * 86400, now))
        self.assertFalse(updater.due(None, now, enabled=False))

    def test_release_info_rejects_prereleases(self):
        from vifte import updater
        info = updater.release_info({"tag_name": "v1.2.0", "body": "x", "html_url": "u"})
        self.assertEqual(info["version"], "1.2.0")
        with self.assertRaises(ValueError):
            updater.release_info({"tag_name": "v2.0.0", "prerelease": True})

    def test_extract_accepts_matching_release(self):
        from vifte import updater
        tar = self.make_tar({"vifte-1.2.0/install.sh": "#!/bin/bash\n",
                             "vifte-1.2.0/vifte/__init__.py": '__version__ = "1.2.0"\n'})
        src = updater.extract(tar, os.path.join(self.dir, "out"), "v1.2.0")
        self.assertTrue(os.path.isfile(os.path.join(src, "install.sh")))

    def test_extract_rejects_path_traversal(self):
        from vifte import updater
        tar = self.make_tar({"vifte-1.2.0/install.sh": "", "../../evil.sh": "rm -rf /"})
        with self.assertRaises(ValueError):
            updater.extract(tar, os.path.join(self.dir, "out"), "v1.2.0")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "evil.sh")))

    def test_extract_rejects_wrong_version(self):
        from vifte import updater
        tar = self.make_tar({"vifte-1.2.0/install.sh": "",
                             "vifte-1.2.0/vifte/__init__.py": '__version__ = "1.1.0"\n'})
        with self.assertRaises(ValueError):
            updater.extract(tar, os.path.join(self.dir, "out"), "v1.2.0")


class TrayMenuTests(unittest.TestCase):
    def test_temperature_labels_update_without_new_layout(self):
        try:
            from vifte.gui.tray import Menu
        except (ImportError, ValueError) as ex:
            self.skipTest(f"GTK not available: {ex}")

        class Conn:
            def __init__(self):
                self.signals = []

            def register_object(self, *a):
                return 1

            def emit_signal(self, _dest, _path, _iface, name, params):
                self.signals.append((name, params.unpack() if params else None))

        conn = Conn()
        menu = Menu(conn, "/test")
        menu.set([{"key": "cpu", "label": "CPU: 45 °C"}, {"separator": True}, {"label": "Quit"}])
        conn.signals.clear()
        menu.update_labels({"cpu": "CPU: 45 °C"})
        self.assertEqual(conn.signals, [], "unchanged text sends nothing")
        menu.update_labels({"cpu": "CPU: 52 °C"})
        self.assertEqual([n for n, _ in conn.signals], ["ItemsPropertiesUpdated"])
        self.assertEqual(conn.signals[0][1][0][0][1]["label"], "CPU: 52 °C")


class AlertTests(unittest.TestCase):
    def test_alert_needs_ten_seconds_and_rearms_below_limit(self):
        from vifte.gui.alerts import AlertMonitor
        a = AlertMonitor()
        rules = [{"sensor": CPU, "temp": 80}]
        temps = {CPU: 85}

        def run(now):
            return a.check(now, rules, temps.get)
        self.assertEqual(run(0), [])
        self.assertEqual(run(5), [], "kort topp: ingen varsel")
        self.assertEqual(run(10), [("temp", rules[0], 85)])
        self.assertEqual(run(20), [], "bare ett varsel")
        temps[CPU] = 79                                  # under grensen, men innenfor slingringsmonnet
        run(21)
        temps[CPU] = 85
        self.assertEqual(run(40), [], "ikke nullstilt før 3 grader under")
        temps[CPU] = 76
        run(41)
        temps[CPU] = 85
        run(42)
        self.assertEqual(len(run(52)), 1)

    def test_emergency_and_lost_sensor_alerts_only_on_change(self):
        from vifte.gui.alerts import AlertMonitor
        a = AlertMonitor()
        self.assertEqual(a.check(0, [], dict().get, True, [FAN0]), [], "ingen varsel om tilstand ved oppstart")
        a = AlertMonitor()
        a.check(0, [], dict().get, False, [])
        self.assertEqual(a.check(1, [], dict().get, True, [FAN0]), [("failsafe",), ("lost", [FAN0])])
        self.assertEqual(a.check(2, [], dict().get, True, [FAN0]), [])


class PairedNameTests(unittest.TestCase):
    def setUp(self):
        try:
            from vifte.gui.common import Model
        except Exception as ex:  # uten GTK
            self.skipTest(f"GTK not available: {ex}")
        e, _ = make_engine()
        cfg = FanControlConfig()
        cfg.ensure_hardware([{"id": s.id, "name": s.name, "kind": s.kind} for s in e.sensors],
                            [{"id": c.id, "name": c.name} for c in e.controls])
        cfg.control(FAN1).update(NickName="AIO Pumpe", PairedFanSensor={"Identifier": "/lpc/nct6798d/fan/1"})
        self.m = Model(None)
        self.m.cfg = cfg
        self.m.names_follow_pairing = True

    def test_speed_sensor_shows_the_paired_fans_name(self):
        self.assertEqual(self.m.display_name("/lpc/nct6798d/fan/1"), "AIO Pumpe")
        self.assertEqual(self.m.display_name("/lpc/nct6798d/fan/0"), "Fan #1", "not paired")
        self.assertEqual(self.m.cfg.sensor_entry("/lpc/nct6798d/fan/1")["NickName"], "Fan #2", "display only")

    def test_own_name_wins_and_setting_can_turn_it_off(self):
        self.m.cfg.sensor_entry("/lpc/nct6798d/fan/1")["NickName"] = "Pumpe RPM"
        self.assertEqual(self.m.display_name("/lpc/nct6798d/fan/1"), "Pumpe RPM")
        self.m.cfg.sensor_entry("/lpc/nct6798d/fan/1")["NickName"] = "Fan #2"
        self.m.names_follow_pairing = False
        self.assertEqual(self.m.display_name("/lpc/nct6798d/fan/1"), "Fan #2")


class MoveCardTests(unittest.TestCase):
    def test_drag_forward_lands_after_and_backward_before(self):
        try:
            from vifte.gui.common import move_entry
        except Exception as ex:
            self.skipTest(f"GTK not available: {ex}")
        lst = [{"Identifier": x} for x in "abcd"]
        order = lambda: "".join(x["Identifier"] for x in lst)   # noqa: E731
        self.assertTrue(move_entry(lst, "a", "c"))
        self.assertEqual(order(), "bcad")
        self.assertTrue(move_entry(lst, "d", "b"))
        self.assertEqual(order(), "dbca")
        self.assertFalse(move_entry(lst, "d", "d"))
        self.assertFalse(move_entry(lst, "x", "a"))


class TranslationTests(unittest.TestCase):
    def test_other_languages_are_complete_and_keep_placeholders(self):
        from vifte import i18n
        for code in sorted(i18n.CODES - {"nb", "en"}):
            table = i18n._table(code)
            self.assertTrue(table, f"no translations for {code}")
            for en in i18n.EN.values():
                with self.subTest(lang=code, text=en[:40]):
                    self.assertIn(en, table)
                    tr = table[en]
                    self.assertEqual(tr.count("{}"), en.count("{}"))
                    self.assertEqual(tr.count("\n"), en.count("\n"))
            unused = set(table) - set(i18n.EN.values())
            self.assertEqual(unused, set(), f"{code}: translations for texts that no longer exist")

    def test_language_selection(self):
        from vifte import i18n
        old = dict(os.environ)
        try:
            for lang, expected in (("de_DE.UTF-8", "de"), ("fr_CA.UTF-8", "fr"), ("es_ES.UTF-8", "es"),
                                   ("nb_NO.UTF-8", "nb"), ("it_IT.UTF-8", "en")):
                for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
                    os.environ.pop(var, None)
                os.environ["LANG"] = lang
                self.assertEqual(i18n.system_language(), expected)
            os.environ["VIFTE_LANG"] = "de"
            i18n._lang = None
            self.assertEqual(i18n._("Lagre"), "Speichern")
            self.assertEqual(i18n._("Laget kurven «{}»").format("CPU"), "Kurve „CPU“ erstellt")
        finally:
            os.environ.clear()
            os.environ.update(old)
            i18n._lang = None

    def test_every_ui_string_has_english_translation(self):
        import ast
        from vifte.i18n import EN
        root = os.path.join(os.path.dirname(__file__), "..", "vifte")
        missing = []
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or not node.args:
                        continue
                    f = node.func
                    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                    arg = node.args[0] if name == "_" else node.args[1] if name in ("_say",) and len(
                        node.args) > 1 else node.args[0] if name == "log_mcp" else None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value not in EN:
                        missing.append(f"{fn}:{node.lineno}: {arg.value!r}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
