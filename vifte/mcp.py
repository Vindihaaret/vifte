"""MCP-server for Claude (stdio, JSON-RPC 2.0).

Startes av Claude Code / Claude Desktop med «vifte mcp». Den er bare et tynt lag:
verktøylisten og alle sikkerhetsregler ligger i tjenesten (mcpapi.py), som også
avviser alle kall når Claude-tilgang er slått av i Vifte.
"""
import json
import sys

from . import __version__
from .client import Client

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """\
Vifte controls the fans of this Linux PC (a FanControl-compatible app). You can read temperatures and
fan speeds, create and tune fan curves, and assign them to fan outputs.

Workflow: call get_status first. Pair fans (pair_fans) if controls have no paired_fan. Build curves on
the most relevant temperature (CPU curves on the CPU core temperature, case fans often on the max of CPU
and GPU via a custom sensor). After changing something, watch get_history for a few minutes before
tuning further. Changes take effect immediately but are not saved until save_configuration – ask the
user before saving. Use undo to revert your last change.

Safety rules are enforced by Vifte and cannot be bypassed: fans you configure never go below the floor
percentage, curves must rise with temperature and reach 100 % by the emergency temperature, and above
that temperature every controlled fan runs at 100 %. If a curve's sensor stops responding, its fans run
at a fixed safe speed until it is back (see controls_with_lost_sensor in get_status). Pumps (AIO or
custom loop) and controls the user has locked have claude_can_change=false: you can read them, but you
can't change, calibrate or pair them, or the curves and sensors they use. If a call is rejected, the error explains what to
change. You cannot delete configurations, pause control or change the safety limits – the user does
that in the app.

Testing under load: Stress is a separate, optional program with its own MCP server ("stress"). If its tools
are available, use them to test curves: run start_test on that server with a steady load, wait with its
get_history until the temperatures have levelled off (usually several minutes), then compare with Vifte's
get_history and fan speeds. Stop the test, adjust the curves here, and repeat the same load to compare.
Stress stops by itself 5 °C below Vifte's emergency temperature. If its tools are not available, ask
the user to run a load instead, or to install Stress (Vifte menu → Download Stress), and never assume
it is installed."""


class Server:
    def __init__(self, client=None, out=None):
        self.client = client or Client()
        self.out = out or sys.stdout

    def send(self, msg):
        self.out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.out.flush()

    def reply(self, mid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": mid}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        self.send(msg)

    def handle(self, msg):
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if mid is None:          # varsel (f.eks. notifications/initialized) – ikke svar
            return
        if method == "initialize":
            wanted = params.get("protocolVersion")
            self.reply(mid, {
                "protocolVersion": wanted if wanted in SUPPORTED else SUPPORTED[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "vifte", "title": "Vifte fan control", "version": __version__},
                "instructions": INSTRUCTIONS,
            })
        elif method == "ping":
            self.reply(mid, {})
        elif method == "tools/list":
            try:
                tools = self.client.call("mcp_tools")["tools"]
            except Exception:
                # Tjenesten kjører ikke (ennå): vis verktøyene likevel; kallene gir en tydelig feil.
                from .mcpapi import McpApi
                tools = McpApi(None).tools()
            self.reply(mid, {"tools": tools})
        elif method == "tools/call":
            name = params.get("name")
            try:
                result = self.client.call("mcp_call", name=name, args=params.get("arguments") or {})["result"]
                text, is_error = json.dumps(result, indent=1, ensure_ascii=False), False
            except OSError as ex:
                text, is_error = f"Vifte service not reachable: {ex}", True
            except Exception as ex:
                text, is_error = str(ex), True
            self.reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        else:
            self.reply(mid, error={"code": -32601, "message": f"method not found: {method}"})

    def run(self, stream=None):
        for line in stream or sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                try:
                    self.handle(m)
                except Exception as ex:  # en feil skal aldri ta ned serveren
                    print(f"vifte mcp: {ex}", file=sys.stderr)
                    if isinstance(m, dict) and m.get("id") is not None:
                        self.reply(m["id"], error={"code": -32603, "message": str(ex)})


def main():
    Server().run()


if __name__ == "__main__":
    main()
