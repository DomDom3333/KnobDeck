"""PC stats for the stats screen (three rings)."""
import glob
import json
import sys
import time
import urllib.request

import psutil

from knobdeck.plugin import Param, Ring, plugin_settings, stats

IS_WIN = sys.platform == "win32"

if IS_WIN:          # Linux reads temperatures directly (psutil / sysfs)
    plugin_settings("stats", "PC stats", [
        Param("lhm_url", "LibreHardwareMonitor URL (optional, for CPU temperature)", "text", "",
              placeholder="http://localhost:8085/data.json",
              help="Windows only reports CPU temperature to tools like LibreHardwareMonitor. Enable its "
                   "'Remote Web Server' (Options menu) and paste the address here."),
    ])

psutil.cpu_percent(None)        # prime the counter so the first reading is meaningful
_nvml = {"h": None, "tried": False}
_lhm = {"t": 0.0, "temp": None}


def _amd_gpu():
    """Linux amdgpu driver: load from sysfs, temperature from hwmon."""
    for busy in glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"):
        try:
            with open(busy) as f:
                load = int(f.read().strip())
        except (OSError, ValueError):
            continue
        temp = None
        for t in glob.glob(busy.rsplit("/", 1)[0] + "/hwmon/hwmon*/temp1_input"):
            try:
                with open(t) as f:
                    temp = round(int(f.read().strip()) / 1000)
                break
            except (OSError, ValueError):
                pass
        return load, temp
    return None


def _gpu():
    if sys.platform.startswith("linux"):
        amd = _amd_gpu()
        if amd:
            return amd
    if not _nvml["tried"]:
        _nvml["tried"] = True
        try:
            import pynvml
            pynvml.nvmlInit()
            _nvml["h"] = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml["m"] = pynvml
        except Exception:
            _nvml["h"] = None
    if not _nvml["h"]:
        return None
    m, h = _nvml["m"], _nvml["h"]
    try:
        return m.nvmlDeviceGetUtilizationRates(h).gpu, m.nvmlDeviceGetTemperature(h, m.NVML_TEMPERATURE_GPU)
    except Exception:
        return None


def _linux_cpu_temp():
    try:
        sensors = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for chip, labels in (("coretemp", ("Package id 0", "Physical id 0")), ("k10temp", ("Tctl", "Tdie")),
                         ("zenpower", ("Tctl", "Tdie")), ("cpu_thermal", ("",)), ("acpitz", ("",)),
                         ("thinkpad", ("CPU",))):
        for entry in sensors.get(chip, []):
            if not labels[0] or entry.label in labels:
                return entry.current
        if sensors.get(chip) and chip in ("coretemp", "k10temp", "zenpower"):
            return sensors[chip][0].current
    return None


def _cpu_temp(url):
    if not IS_WIN:
        return _linux_cpu_temp() if sys.platform.startswith("linux") else None
    if not url:
        return None
    if time.monotonic() - _lhm["t"] < 3:
        return _lhm["temp"]
    _lhm["t"] = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=0.8) as r:
            tree = json.load(r)
    except Exception:
        _lhm["temp"] = None
        return None
    wanted = ("CPU Package", "Core (Tctl/Tdie)", "CPU (Tctl/Tdie)", "Core Average", "CPU Core")

    def walk(n, out):
        if any(n.get("Text", "").startswith(w) for w in wanted) and "°C" in str(n.get("Value", "")):
            out.append(n)
        for c in n.get("Children", []):
            walk(c, out)
        return out

    found = walk(tree, [])
    try:
        _lhm["temp"] = float(found[0]["Value"].split()[0].replace(",", ".")) if found else None
    except (ValueError, IndexError):
        _lhm["temp"] = None
    return _lhm["temp"]


@stats("stats.system", "CPU / GPU / RAM", icon="sym:charge")
def system(ctx, p):
    cpu = psutil.cpu_percent(None)
    t = _cpu_temp(ctx.settings("stats").get("lhm_url"))
    deg = "°C" if ctx.has("utf8") else "C"
    rings = [Ring("CPU", cpu, f"{cpu:.0f}%" + (f" {t:.0f}{deg}" if t else ""))]
    g = _gpu()
    if g:
        rings.append(Ring("GPU", g[0], f"{g[0]}%" + (f" {g[1]}{deg}" if g[1] is not None else "")))
    vm = psutil.virtual_memory()
    rings.append(Ring("RAM", vm.percent, f"{vm.percent:.0f}% {vm.used / 2**30:.1f}G"))
    return rings


_net = {"t": 0.0, "rx": 0, "tx": 0}


@stats("stats.network", "Network + disk", icon="sym:wifi",
       params=[Param("max_mbps", "Line speed (Mbit/s) = full ring", "number", 100),
               Param("drive", "Drive", "text", "C:\\" if IS_WIN else "/")])
def network(ctx, p):
    io = psutil.net_io_counters()
    now = time.monotonic()
    dt = now - _net["t"] if _net["t"] else 0
    rx = (io.bytes_recv - _net["rx"]) * 8 / dt / 1e6 if dt else 0
    tx = (io.bytes_sent - _net["tx"]) * 8 / dt / 1e6 if dt else 0
    _net.update(t=now, rx=io.bytes_recv, tx=io.bytes_sent)
    mx = float(p["max_mbps"]) or 100
    try:
        du = psutil.disk_usage(p["drive"])
        disk = Ring("Disk", du.percent, f"{du.percent:.0f}% {du.free / 2**30:.0f}G free")
    except Exception:
        disk = Ring("Disk", 0, "n/a")
    return [Ring("Down", min(rx, mx), f"{rx:.1f} Mb/s", max=mx),
            Ring("Up", min(tx, mx), f"{tx:.1f} Mb/s", max=mx),
            disk]
