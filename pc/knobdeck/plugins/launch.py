"""Open programs, files, folders and websites; run commands."""
import subprocess

from knobdeck import osutil
from knobdeck.plugin import Param, action

PLACEHOLDER = (r"C:\Program Files\...\app.exe  or  https://..." if osutil.IS_WIN
               else "firefox, ~/Documents, /usr/share/applications/x.desktop  or  https://...")


@action("launch.open", "Open app / file / website", group="Launch", icon="sym:file",
        params=[Param("target", "Program, file, folder or URL", "file", "", placeholder=PLACEHOLDER),
                Param("args", "Arguments (optional)", "text", "")])
def open_target(ctx, p):
    osutil.open_target(p["target"], p.get("args") or "")


@action("launch.command", "Run command", group="Launch", icon="sym:enter",
        params=[Param("command", "Command line", "text", "", placeholder="powershell -File C:\\scripts\\x.ps1" if osutil.IS_WIN else "~/bin/myscript.sh"),
                Param("hidden", "Hide the console window (Windows)", "bool", True)])
def run_command(ctx, p):
    if not p["command"].strip():
        raise ValueError("no command")
    subprocess.Popen(p["command"], shell=True, creationflags=osutil.NO_WINDOW if p.get("hidden") else 0,
                     start_new_session=not osutil.IS_WIN)
