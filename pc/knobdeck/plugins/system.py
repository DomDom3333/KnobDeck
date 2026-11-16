"""System functions (Windows and Linux)."""
from knobdeck import osutil
from knobdeck.plugin import action


@action("system.lock", "Lock PC", group="System", icon="sym:power")
def lock(ctx, p):
    osutil.lock_screen()


@action("system.sleep", "Sleep", group="System", icon="sym:power")
def sleep(ctx, p):
    osutil.suspend()


@action("system.desktop", "Show desktop", group="System", icon="sym:home",
        help="Sends Win+D (Linux: Super+D - in GNOME set it under Settings > Keyboard > Shortcuts > "
             "Navigation > Hide all normal windows).")
def desktop(ctx, p):
    osutil.show_desktop()


@action("system.screenshot", "Screenshot (snip)", group="System", icon="sym:image")
def screenshot(ctx, p):
    osutil.screenshot()


@action("system.taskmgr", "Task Manager", group="System", icon="sym:bars",
        help="Windows: Task Manager. Linux: the desktop's system monitor app.")
def taskmgr(ctx, p):
    osutil.task_manager()


@action("system.monitor_off", "Screen off", group="System", icon="sym:eye_off")
def monitor_off(ctx, p):
    osutil.monitor_off()
