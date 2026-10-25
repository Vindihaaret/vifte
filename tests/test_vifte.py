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


class TranslationTests(unittest.TestCase):
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
