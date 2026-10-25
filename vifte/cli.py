"""Kommandolinje: vifte [-c konfig.json] [status|list|save|pause|resume]."""
import argparse
import os
import sys

from .client import Client
from .i18n import _


def main():
    ap = argparse.ArgumentParser(prog="vifte", description=_("Vifte – viftestyring (FanControl for Linux)"))
    ap.add_argument("-c", "--config", help=_("bytt til (eller importer og bytt til) denne konfigurasjonen"))
    ap.add_argument("-m", "--minimized", action="store_true", help=_("vis bare ikonet i statusfeltet"))
    ap.add_argument("-w", "--window", action="store_true", help=_("åpne vinduet"))
    ap.add_argument("-r", "--refresh", action="store_true", help=_("søk etter maskinvare på nytt"))
    ap.add_argument("command", nargs="?", choices=["status", "list", "save", "pause", "resume", "gui", "tray", "mcp", "uninstall"])
    ap.add_argument("--purge", action="store_true", help=_("slett også konfigurasjonene ved avinstallering"))
    a = ap.parse_args()
    if a.command == "mcp":
        # MCP-server for Claude; stdout er reservert for protokollen.
        from .mcp import main as mcp_main
        return mcp_main()
    if a.command == "uninstall":
        tool = "/usr/local/bin/vifte-uninstall"
        if not os.path.exists(tool):
            sys.exit(_("Vifte ser ikke ut til å være installert (fant ikke {}).").format(tool))
        argv = [tool] + (["--purge"] if a.purge else [])
        if os.geteuid() != 0:
            argv = ["sudo"] + argv
        os.execvp(argv[0], argv)
    c = Client()
    try:
        if a.refresh:
            c.call("rescan")
        if a.config:
            if os.path.isfile(a.config):
                with open(a.config, encoding="utf-8-sig") as f:
                    r = c.call("import", name=os.path.basename(a.config), text=f.read())
                print(_("Importert og aktivert: {}").format(r["name"]))
            else:
                c.call("load", name=a.config)
                print(_("Aktivert: {}").format(a.config))
        if a.command == "status":
            s = c.call("state")
            print(_("Konfigurasjon: {}").format(s["current"]) + (_(" (ulagret)") if s["dirty"] else ""))
            names = {x["id"]: x["name"] for x in s["sensors"] + s["controls"]}
            for ident, st in s["control_state"].items():
                if st.get("out") is not None:
                    rpm = f"  {st['rpm']:.0f} RPM" if st.get("rpm") is not None else ""
                    print(f"  {names.get(ident, ident):35s} {st['out']:5.1f} %{rpm}")
            for x in s["sensors"]:
                if x["kind"] == "temperature" and x["value"] is not None:
                    print(f"  {x['hardware'] + ' / ' + x['name']:45s} {x['value']:6.1f} °C")
            if s["missing"]:
                print(_("Manglende sensorer:"), ", ".join(s["missing"]))
        elif a.command == "list":
            s = c.call("state")
            for n in s["configs"]:
                print(("* " if n == s["current"] else "  ") + n)
        elif a.command == "save":
            c.call("save")
        elif a.command in ("pause", "resume"):
            c.call("settings", paused=a.command == "pause")
        elif a.command in ("gui", "tray") or a.window or a.minimized or (
                not a.command and not a.config and not a.refresh):
            from .gui import main as gui_main
            return gui_main(minimized=(a.command == "tray" or a.minimized) and not a.window)
    except OSError as e:
        sys.exit(_("Får ikke kontakt med vifted ({}): {}\n"
                   "Start tjenesten: sudo systemctl start vifted").format(c.path, e))
    except Exception as e:
        sys.exit(_("Feil: {}").format(_(str(e))))


if __name__ == "__main__":
    main()
