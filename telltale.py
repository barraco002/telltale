#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telltale — a tiny always-on-top hardware gauge for Windows that stays quiet
until your PC needs you.

One file, plain tkinter + psutil (plus pynvml / wmi when available):

* A small borderless panel that stays on top, drawn on a single Canvas. Drag
  it from anywhere; the corner grip (or Ctrl+− / Ctrl+Plus / Ctrl+0) scales
  the whole panel and the height follows the content. Scale and position are
  remembered in %APPDATA%\\Telltale\\settings.json.
* The first line is the headline: "All good", or the advice of the moment.
* Three segmented meters with a peak marker for the last minute. They all read
  the same way: the fuller, the harder the PC is working.
    CPU  usage (and temperature, when a sensor is available)
    RAM  free memory; the meter shows what is in use
    GPU  temperature (or usage, if the driver reports it); danger zone marked
* Two levels of advice (see Advisor):
  - "Give your PC a break": sustained heat or load. A suggestion, in amber.
  - "Urgent: close or throttle apps": heat that endangers the hardware, or
    critically low free RAM. Names the heaviest processes. In red.
  Telltale never closes or throttles anything by itself, and when the
  condition clears the advice simply disappears — no "recovered" noise.
* Sensors are read on a background thread; the UI only paints the latest
  snapshot, so it never freezes.

Usage:

    python telltale.py               # open the panel
    python telltale.py --self-test   # print sensor diagnostics and exit
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import os
import platform
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import psutil

try:
    import pynvml
except ImportError:  # no NVIDIA (or no nvidia-ml-py): no GPU row
    pynvml = None

try:
    import wmi
except ImportError:  # wmi needs pywin32; without it there is no CPU temperature
    wmi = None

__version__ = "1.0.0"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

APP_NAME = "Telltale"
MIN_SCALE, MAX_SCALE = 0.75, 2.0   # limits of the panel scale

SENSOR_INTERVAL = 1.0              # seconds between sensor readings
POLL_MS = 300                      # how often the UI picks up the latest snapshot
HISTORY = 60                       # samples behind the peak marker (one minute)
STALE_S = 4.0                      # no fresh readings for this long: the headline says so

ALERT_ON = 85.0                    # °C: a reading turns red immediately
ALERT_OFF = 80.0                   # °C: below this it stops being red
TEMP_MIN, TEMP_MAX = 10.0, 125.0   # plausible range, to drop garbage readings
TEMP_SCALE = (30.0, 100.0)         # range of the temperature meter

# Advice. "hold" is how many seconds in a row the condition must be met;
# "off" is the exit threshold (hysteresis, so the advice doesn't flicker).
REST_HEAT_C, REST_HEAT_OFF_C, REST_HEAT_HOLD = 80.0, 76.0, 30.0
REST_LOAD_PCT, REST_LOAD_OFF_PCT, REST_LOAD_HOLD = 90.0, 80.0, 60.0
URGENT_HEAT_C, URGENT_HEAT_OFF_C, URGENT_HEAT_HOLD = 90.0, 86.0, 5.0
URGENT_RAM_MB, URGENT_RAM_OFF_MB, URGENT_RAM_HOLD = 300.0, 360.0, 5.0

# Palette: a graphite faceplate; color only shows up when there is something to say.
COLOR_FACE = "#1b2025"             # faceplate
COLOR_EDGE = "#30383f"             # 1 px border
COLOR_INK = "#e7ebee"              # main text and figures
COLOR_INK_2 = "#97a3ad"            # labels and units
COLOR_INK_3 = "#5f6b75"            # idle controls
COLOR_TRACK = "#262d33"            # unlit segment
COLOR_DANGER_TRACK = "#3b2828"     # unlit segment inside the danger zone
COLOR_PEAK = "#4f5b66"             # last-minute peak marker
COLOR_CALM = "#62a8dc"             # lit segment at rest
COLOR_OK = "#5fbf86"               # "all good" light
COLOR_REST = "#e3a63f"             # amber: take a break
COLOR_REST_TINT = "#2a2519"
COLOR_URGENT = "#ec5b52"           # red: urgent
COLOR_URGENT_TINT = "#321f1e"
COLOR_HOVER = "#2b333a"
COLOR_CLOSE_HOVER = "#a33a33"

FONTS = {                          # (family, points at scale 1)
    "status": ("Bahnschrift SemiBold SemiConden", 11),  # narrow DIN: the headline fits on one line
    "text": ("Bahnschrift", 10),
    "label": ("Bahnschrift", 10),
    "value": ("Bahnschrift SemiBold", 17),
    "unit": ("Bahnschrift", 10),
    "icon": ("Segoe MDL2 Assets", 8),
}
ICON_PIN, ICON_PINNED, ICON_CLOSE = "", "", ""

# Sizes in px at 96 DPI and scale 1.
BASE_W = 300
PAD = 14
INSET = 6                          # margin of the corner buttons
BTN_W, BTN_H = 26, 22
LED = 7
SEG_H, SEG_GAP, SEG_PITCH = 8, 2, 9
ROW_GAP = 13

LEVEL_OK, LEVEL_REST, LEVEL_URGENT = "ok", "rest", "urgent"

MB = 1024 * 1024
APP_DIR = Path(__file__).resolve().parent
ICON_PATH = APP_DIR / "assets" / "telltale.ico"
SETTINGS_FILE = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "settings.json"
LEGACY_SETTINGS_FILE = APP_DIR / "settings.json"  # where earlier versions kept them


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------

def _fmt_temp(value: Optional[float]) -> str:
    """Formatted temperature, or "N/A" when there is no reading."""
    return "N/A" if value is None else f"{value:.0f} °C"


def _fmt_mb(mb: float) -> str:
    """Human-readable megabytes or gigabytes."""
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _fmt_percent(value: float) -> str:
    return f"{value:.0f}%"


def _ellipsize(text: str, limit: int) -> str:
    """Shortens a long text with "…"."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: max(1, limit - 1)].rstrip() + "…"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _temp_fraction(celsius: float) -> float:
    low, high = TEMP_SCALE
    return _clamp((celsius - low) / (high - low))


def _cpu_model_name() -> str:
    """Marketing name of the CPU (Windows registry; generic fallback)."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                cleaned = " ".join(str(name).split())
                if cleaned:
                    return cleaned
        except Exception:
            pass
    return platform.processor() or platform.machine() or "CPU"


def _clean_proc_name(name: str) -> str:
    return re.sub(r"\.exe$", "", name, flags=re.IGNORECASE) or name


def _valid_celsius(value: Optional[float]) -> Optional[float]:
    """Drops impossible readings (0 K, sensors stuck at 0, 200 °C spikes...)."""
    if value is None:
        return None
    return value if TEMP_MIN <= value <= TEMP_MAX else None


def _load_settings() -> dict:
    for path in (SETTINGS_FILE, LEGACY_SETTINGS_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # without write access the panel works the same, it just won't remember


def _enable_dpi_awareness() -> None:
    """Crisp at 125–200 %: Windows won't stretch the panel like a bitmap."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _wrap(text: str, font: tkfont.Font, width: int) -> list[str]:
    """Splits text into lines that fit in `width` px, breaking at words."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.measure(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _wrap_balanced(text: str, font: tkfont.Font, width: int) -> list[str]:
    """Like _wrap, but with even lines: never a lone word on the last one."""
    lines = _wrap(text, font, width)
    if len(lines) < 2:
        return lines
    low, high = width // 2, width
    while low < high:  # the narrowest width that keeps the same number of lines
        middle = (low + high) // 2
        if len(_wrap(text, font, middle)) <= len(lines):
            high = middle
        else:
            low = middle + 1
    return _wrap(text, font, high)


def _segments(width: int, unit: float) -> tuple[int, int, int]:
    """(count, width, gap) of segments that fill `width` with no visible leftover."""
    gap = max(1, round(SEG_GAP * unit))
    target = max(8, round(width / (SEG_PITCH * unit)))
    best: Optional[tuple[tuple[int, int], int, int]] = None
    for count in range(max(8, target - 4), target + 5):
        seg = (width - (count - 1) * gap) // count
        if seg < 2:
            continue
        leftover = width - (count * seg + (count - 1) * gap)
        key = (leftover, abs(count - target))
        if best is None or key < best[0]:
            best = (key, count, seg)
    if best is None:
        return 8, max(2, (width - 7 * gap) // 8), gap
    return best[1], best[2], gap


# --------------------------------------------------------------------------
# Processes (only queried while an urgent advice is showing)
# --------------------------------------------------------------------------

def _ram_mb_by_name() -> dict[str, float]:
    """RAM in use (MB) per process name."""
    totals: dict[str, float] = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        memory = proc.info.get("memory_info")
        if memory is None or proc.pid == 0:
            continue
        name = _clean_proc_name(proc.info.get("name") or "?")
        totals[name] = totals.get(name, 0.0) + memory.rss / MB
    return totals


def _cpu_seconds_by_pid() -> dict[int, tuple[str, float]]:
    """Accumulated CPU seconds per process: pid → (name, seconds)."""
    seconds: dict[int, tuple[str, float]] = {}
    for proc in psutil.process_iter(["name", "cpu_times"]):
        times = proc.info.get("cpu_times")
        if times is None or proc.pid == 0:  # pid 0 is "System Idle", not load
            continue
        name = _clean_proc_name(proc.info.get("name") or "?")
        seconds[proc.pid] = (name, times.user + times.system)
    return seconds


# --------------------------------------------------------------------------
# CPU temperature
# --------------------------------------------------------------------------

class CpuThermal:
    """CPU temperature from several sources, most reliable first.

    Order: LibreHardwareMonitor/OpenHardwareMonitor (WMI), the system's ACPI
    zone and, as a last resort, psutil (useful outside Windows). A source that
    fails is put on hold before retrying, so the app doesn't keep querying a
    sensor that isn't there.
    """

    RETRY_SECONDS = 45.0
    MONITOR_NAMESPACES = (r"root\LibreHardwareMonitor", r"root\OpenHardwareMonitor")

    def __init__(self) -> None:
        self.source = "none"
        self.last_error = ""
        self._monitor_conn = None
        self._monitor_name = ""
        self._monitor_retry_at = 0.0
        self._acpi_conn = None
        self._acpi_retry_at = 0.0

    def read(self) -> Optional[float]:
        """Best available temperature, or None when there is no sensor."""
        for provider in (self._read_hardware_monitor, self._read_acpi, self._read_psutil):
            try:
                value = provider()
            except Exception as exc:  # one broken provider must not take down the rest
                self.last_error = f"{type(exc).__name__}: {exc}"
                continue
            value = _valid_celsius(value)
            if value is not None:
                return value
        return None

    def _read_hardware_monitor(self) -> Optional[float]:
        """LibreHardwareMonitor or OpenHardwareMonitor sensors over WMI."""
        if wmi is None:
            return None

        if self._monitor_conn is None:
            now = time.monotonic()
            if now < self._monitor_retry_at:
                return None
            for namespace in self.MONITOR_NAMESPACES:
                try:
                    connection = wmi.WMI(namespace=namespace)
                    connection.Sensor()  # checks that the provider exposes data
                except Exception:
                    continue
                self._monitor_conn = connection
                self._monitor_name = namespace.rsplit("\\", 1)[-1]
                break
            if self._monitor_conn is None:
                self._monitor_retry_at = time.monotonic() + self.RETRY_SECONDS
                return None

        readings = []
        for sensor in self._monitor_conn.Sensor():
            try:
                if str(sensor.SensorType) != "Temperature":
                    continue
                value = float(sensor.Value)
            except Exception:
                continue
            value = _valid_celsius(value)
            if value is not None:
                readings.append(value)

        if readings:
            self.source = self._monitor_name
            return max(readings)
        return None

    def _read_acpi(self) -> Optional[float]:
        """MSAcpi_ThermalZoneTemperature: tenths of a kelvin → celsius."""
        if wmi is None:
            return None

        now = time.monotonic()
        if self._acpi_conn is None:
            if now < self._acpi_retry_at:
                return None
            try:
                connection = wmi.WMI(namespace=r"root\wmi")
                connection.MSAcpi_ThermalZoneTemperature()  # checks the class exists
            except Exception:
                self._acpi_retry_at = now + self.RETRY_SECONDS
                return None
            self._acpi_conn = connection

        try:
            zones = self._acpi_conn.MSAcpi_ThermalZoneTemperature()
        except Exception:
            self._acpi_conn = None
            self._acpi_retry_at = now + self.RETRY_SECONDS
            return None

        readings = []
        for zone in zones:
            try:
                celsius = float(zone.CurrentTemperature) / 10.0 - 273.15
            except Exception:
                continue
            celsius = _valid_celsius(celsius)
            if celsius is not None:
                readings.append(celsius)

        if readings:
            self.source = "ACPI"
            return max(readings)
        return None

    def close(self) -> None:
        """Releases the COM connections before the thread shuts COM down."""
        self._monitor_conn = None
        self._acpi_conn = None

    def _read_psutil(self) -> Optional[float]:
        """Cross-platform fallback (on Windows it usually returns nothing)."""
        getter = getattr(psutil, "sensors_temperatures", None)
        if getter is None:
            return None
        try:
            groups = getter()
        except Exception:
            return None
        readings = []
        for entries in groups.values():
            for entry in entries:
                current = getattr(entry, "current", None)
                if current is None:
                    continue
                current = _valid_celsius(float(current))
                if current is not None:
                    readings.append(current)
        if readings:
            self.source = "psutil"
            return max(readings)
        return None


# --------------------------------------------------------------------------
# GPU (NVIDIA)
# --------------------------------------------------------------------------

class GpuReader:
    """Wraps pynvml and tolerates every failure.

    Without NVML, without an NVIDIA GPU, or when the driver fails, available
    stays False and the panel hides the GPU row. Old cards' drivers don't
    report utilization and NVML raises: util_reported becomes False and the
    panel shows the temperature instead.
    """

    def __init__(self) -> None:
        self.available = False
        self.name = ""
        self.util_reported = True
        self.last_error = ""
        self._handle = None
        self._opened = False

    def open(self) -> None:
        """Initializes NVML once (called from the sensor thread)."""
        if self._opened:
            return
        self._opened = True
        if pynvml is None:
            self.last_error = "nvidia-ml-py not installed"
            return
        try:
            pynvml.nvmlInit()
            if pynvml.nvmlDeviceGetCount() < 1:
                raise RuntimeError("NVML found no GPU")
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            raw_name = pynvml.nvmlDeviceGetName(self._handle)
            self.name = raw_name.decode("utf-8", "replace") if isinstance(raw_name, bytes) else str(raw_name)
            self.available = True
        except Exception as exc:
            self.available = False
            self._handle = None
            self.last_error = f"{type(exc).__name__}: {exc}"

    def read(self) -> tuple[float, Optional[float]]:
        """Returns (usage %, temperature °C or None)."""
        if not self.available or self._handle is None:
            return 0.0, None

        try:
            utilisation = float(pynvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
            self.util_reported = True
        except Exception:
            utilisation = 0.0
            self.util_reported = False

        temperature = None
        try:
            raw_temp = float(pynvml.nvmlDeviceGetTemperature(self._handle, pynvml.NVML_TEMPERATURE_GPU))
            temperature = _valid_celsius(raw_temp)
        except Exception:
            temperature = None

        return max(0.0, min(100.0, utilisation)), temperature

    def close(self) -> None:
        if self.available:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
        self.available = False
        self._handle = None


# --------------------------------------------------------------------------
# Advice: thresholds held over time
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Advice:
    """What the headline says. "ok" means there is nothing to say.

    causes: what triggered it ("cpu-load", "ram", "cpu-heat", "gpu-heat"), so
    the matching meter can be tinted.
    """

    level: str = LEVEL_OK
    title: str = ""
    text: str = ""
    causes: tuple[str, ...] = ()


class _Trigger:
    """Turns on once the condition has held for `hold` seconds in a row and turns
    off only when the value crosses the exit threshold (hysteresis, no flicker)."""

    def __init__(self, hold: float, above: bool) -> None:
        self._hold = hold
        self._above = above
        self._since: Optional[float] = None
        self.active = False

    def update(self, value: Optional[float], stamp: float, on: float, off: float) -> bool:
        if value is None:  # no reading, no advice
            self._since = None
            self.active = False
            return False
        if self.active:
            leaving = value < off if self._above else value > off
            if leaving:
                self.active = False
                self._since = None
            return self.active
        met = value >= on if self._above else value <= on
        if not met:
            self._since = None
            return False
        if self._since is None:
            self._since = stamp
        self.active = stamp - self._since >= self._hold
        return self.active


_DEVICE = {"cpu": "The CPU", "gpu": "The GPU"}


class Advisor:
    """Decides whether to suggest a break or raise an urgent alert.

    It only looks at readings and how long they have lasted. It never closes or
    throttles anything: that is the user's call. No Tk here, so it is tested
    by tests/test_advisor.py.
    """

    def __init__(self) -> None:
        self._heat_rest = _Trigger(REST_HEAT_HOLD, above=True)
        self._load_rest = _Trigger(REST_LOAD_HOLD, above=True)
        self._heat_urgent = _Trigger(URGENT_HEAT_HOLD, above=True)
        self._ram_urgent = _Trigger(URGENT_RAM_HOLD, above=False)

    def update(self, snap: "Snapshot", top: Callable[[str], str]) -> Advice:
        temps = {side: t for side, t in (("cpu", snap.cpu_temp), ("gpu", snap.gpu_temp)) if t is not None}
        side = max(temps, key=temps.get) if temps else "cpu"
        hottest = temps.get(side)

        heat_rest = self._heat_rest.update(hottest, snap.stamp, REST_HEAT_C, REST_HEAT_OFF_C)
        load_rest = self._load_rest.update(snap.cpu_percent, snap.stamp, REST_LOAD_PCT, REST_LOAD_OFF_PCT)
        heat_urgent = self._heat_urgent.update(hottest, snap.stamp, URGENT_HEAT_C, URGENT_HEAT_OFF_C)
        ram_urgent = self._ram_urgent.update(
            snap.ram_available_mb, snap.stamp, URGENT_RAM_MB, URGENT_RAM_OFF_MB
        )

        if heat_urgent or ram_urgent:
            sentences, causes = [], []
            if heat_urgent:
                sentences.append(f"{_DEVICE[side]} is at {hottest:.0f} °C.")
                causes.append(f"{side}-heat")
            if ram_urgent:
                sentences.append(f"Only {_fmt_mb(snap.ram_available_mb)} of RAM left.")
                causes.append("ram")
            by = "ram" if ram_urgent else "cpu"
            procs = top(by)
            if procs:
                sentences.append(f"Top {'RAM' if by == 'ram' else 'CPU'} users: {procs}.")
            return Advice(LEVEL_URGENT, "Urgent: close or throttle apps", " ".join(sentences), tuple(causes))

        if heat_rest:
            return Advice(
                LEVEL_REST,
                "Give your PC a break",
                f"{_DEVICE[side]} has been at {hottest:.0f} °C for a while. It's fine, in theory.",
                (f"{side}-heat",),
            )
        if load_rest:
            return Advice(
                LEVEL_REST,
                "Give your PC a break",
                f"The CPU has been at {snap.cpu_percent:.0f}% for over a minute. It's fine, in theory.",
                ("cpu-load",),
            )
        return Advice()


# --------------------------------------------------------------------------
# Snapshot and sensor thread
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Snapshot:
    """One complete reading of every sensor."""

    cpu_percent: float = 0.0
    cpu_temp: Optional[float] = None
    cpu_source: str = "none"
    cpu_name: str = "CPU"
    gpu_percent: float = 0.0
    gpu_temp: Optional[float] = None
    gpu_present: bool = False
    gpu_util_reported: bool = True
    gpu_name: str = ""
    ram_available_mb: Optional[float] = None
    ram_total_mb: Optional[float] = None
    notes: tuple[str, ...] = ()
    advice: Advice = field(default_factory=Advice)
    stamp: float = 0.0


class SensorHub:
    """Groups CPU, GPU and RAM. All state lives on the thread that creates it."""

    def __init__(self) -> None:
        self.cpu_name = _cpu_model_name()
        self.thermal = CpuThermal()
        self.gpu = GpuReader()
        self._gpu_opened = False
        psutil.cpu_percent(interval=None)  # priming: the first call always returns 0

    def _ensure_gpu(self) -> None:
        if not self._gpu_opened:
            self.gpu.open()
            self._gpu_opened = True

    def sample(self) -> Snapshot:
        self._ensure_gpu()

        try:
            cpu_percent = float(psutil.cpu_percent(interval=None))
        except Exception:
            cpu_percent = 0.0

        try:
            cpu_temp = self.thermal.read()
        except Exception:
            cpu_temp = None

        gpu_percent, gpu_temp = self.gpu.read()

        ram_available_mb: Optional[float] = None
        ram_total_mb: Optional[float] = None
        try:
            memory = psutil.virtual_memory()
            ram_available_mb, ram_total_mb = memory.available / MB, memory.total / MB
        except Exception:
            pass

        notes = []
        if not self.gpu.available:
            notes.append("no NVIDIA GPU")
        if cpu_temp is None:
            notes.append("CPU temp N/A")

        return Snapshot(
            cpu_percent=cpu_percent,
            cpu_temp=cpu_temp,
            cpu_source=self.thermal.source,
            cpu_name=self.cpu_name,
            gpu_percent=gpu_percent,
            gpu_temp=gpu_temp,
            gpu_present=self.gpu.available,
            gpu_util_reported=self.gpu.util_reported,
            gpu_name=self.gpu.name,
            ram_available_mb=ram_available_mb,
            ram_total_mb=ram_total_mb,
            notes=tuple(notes),
            stamp=time.time(),
        )

    def top_processes(self, by: str) -> str:
        """The two processes using the most RAM ("ram") or CPU ("cpu"), as short text."""
        try:
            if by == "ram":
                shares = _ram_mb_by_name()
                fmt = _fmt_mb
            else:
                shares = self._cpu_percent_by_name()
                fmt = _fmt_percent
        except Exception:
            return ""
        best = sorted(shares.items(), key=lambda item: item[1], reverse=True)[:2]
        return ", ".join(f"{_ellipsize(name, 16)} {fmt(value)}" for name, value in best if value >= 0.5)

    def _cpu_percent_by_name(self) -> dict[str, float]:
        """CPU usage per process (% of the whole CPU), measured over ~0.3 s."""
        before = _cpu_seconds_by_pid()
        started = time.monotonic()
        time.sleep(0.3)
        elapsed = max(0.05, time.monotonic() - started)
        after = _cpu_seconds_by_pid()
        cores = psutil.cpu_count() or 1
        totals: dict[str, float] = {}
        for pid, (name, seconds) in after.items():
            if pid not in before:
                continue
            used = seconds - before[pid][1]
            if used > 0:
                totals[name] = totals.get(name, 0.0) + used / (elapsed * cores) * 100.0
        return totals

    def close(self) -> None:
        self.gpu.close()
        self.thermal.close()


class SensorWorker(threading.Thread):
    """Daemon thread that samples and leaves the latest Snapshot in a queue."""

    def __init__(self, sink: "queue.Queue[Snapshot]", interval: float = SENSOR_INTERVAL) -> None:
        super().__init__(name="sensor-worker", daemon=True)
        self._sink = sink
        self._interval = max(0.2, float(interval))
        self._stop_event = threading.Event()
        self.error = ""

    def run(self) -> None:
        hub: Optional[SensorHub] = None
        advisor = Advisor()
        try:
            if sys.platform == "win32":
                try:
                    import pythoncom

                    pythoncom.CoInitialize()  # WMI uses COM: every thread must initialize it
                except Exception:
                    pass

            hub = SensorHub()
            self._stop_event.wait(min(0.4, self._interval))

            while not self._stop_event.is_set():
                started = time.monotonic()
                try:
                    snapshot = hub.sample()
                    snapshot.advice = advisor.update(snapshot, hub.top_processes)
                except Exception as exc:
                    snapshot = Snapshot(
                        stamp=time.time(),
                        notes=(f"sampling error: {type(exc).__name__}: {exc}",),
                    )
                self._publish(snapshot)
                remaining = self._interval - (time.monotonic() - started)
                self._stop_event.wait(max(0.05, remaining))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self._publish(Snapshot(stamp=time.time(), notes=(f"sensors stopped: {self.error}",)))
        finally:
            if hub is not None:
                hub.close()
                del hub
                gc.collect()  # release the COM objects before the thread dies
            # COM is left initialized on purpose: the thread ends right after and
            # CoUninitialize() makes pywin32 complain while releasing WMI objects.

    def _publish(self, snapshot: Snapshot) -> None:
        """Replaces the pending snapshot: the UI only cares about the latest one."""
        try:
            while True:
                self._sink.get_nowait()
        except queue.Empty:
            pass
        try:
            self._sink.put_nowait(snapshot)
        except queue.Full:
            pass

    def stop(self) -> None:
        self._stop_event.set()


# --------------------------------------------------------------------------
# UI: an instrument drawn on a single Canvas
# --------------------------------------------------------------------------

@dataclass
class _Row:
    """One row of the panel: label, figure and meter."""

    key: str
    label: str
    number: str
    unit: str = ""
    aside: str = ""
    aside_color: str = COLOR_INK_2
    fraction: float = 0.0
    peak: float = 0.0
    tone: str = COLOR_CALM
    danger_from: Optional[float] = None


class MonitorApp(tk.Tk):
    """Borderless always-on-top panel that can be dragged from anywhere."""

    def __init__(self, start_topmost: bool = True, persist: bool = True) -> None:
        _enable_dpi_awareness()
        super().__init__()
        self.withdraw()  # shown at its final size, without a flash
        self._closing = False
        self._persist = bool(persist)
        self._topmost = bool(start_topmost)
        self._queue: "queue.Queue[Snapshot]" = queue.Queue(maxsize=1)
        self._worker: Optional[SensorWorker] = None
        self._snapshot: Optional[Snapshot] = None
        self._history = {key: deque(maxlen=HISTORY) for key in ("cpu", "ram", "gpu")}
        self._hot = {"cpu": False, "gpu": False}
        self._hits: dict[str, tuple[int, int, int, int]] = {}
        self._hover: Optional[str] = None
        self._press: Optional[str] = None
        self._drag_anchor = (0, 0)
        self._drag_window = (0, 0)
        self._resize_anchor = (0, 0)
        self._resize_from = (1.0, 1, 1)
        self._pending_scale: Optional[float] = None
        self._scale_job: Optional[str] = None
        self._size: Optional[tuple[int, int]] = None

        self.title(APP_NAME)
        self._apply_window_icon()  # own icon in Alt+Tab and in shortcuts
        self.overrideredirect(True)  # no Windows title bar
        self.attributes("-topmost", self._topmost)
        self.configure(bg=COLOR_FACE)

        settings = _load_settings() if self._persist else {}
        self._dpi = self._dpi_factor()
        scale = _as_float(settings.get("scale"), 0.0)
        if not scale and "width" in settings:  # earlier versions stored the width, not the scale
            scale = _as_float(settings["width"], BASE_W * self._dpi) / (BASE_W * self._dpi)
        self._scale = _clamp(scale or 1.0, MIN_SCALE, MAX_SCALE)
        self._fonts = {
            name: tkfont.Font(root=self, family=family, size=size) for name, (family, size) in FONTS.items()
        }
        self._apply_font_scale()
        self._pos = self._startup_position(settings)

        self._canvas = tk.Canvas(self, bg=COLOR_FACE, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self._bind_events()
        self._redraw()
        self.deiconify()
        self._apply_rounded_corners()
        self._start_sensors()
        self.after(POLL_MS, self._poll)

    # -- window, scale and settings -----------------------------------------

    @property
    def _unit(self) -> float:
        """Pixels per design px: chosen scale × screen DPI."""
        return self._scale * self._dpi

    def _dpi_factor(self) -> float:
        try:
            factor = float(self.winfo_fpixels("1i")) / 96.0
        except Exception:
            return 1.0
        return factor if 0.75 <= factor <= 3.0 else 1.0

    def _apply_window_icon(self) -> None:
        """Loads assets/telltale.ico when present; without it the app runs iconless."""
        try:
            if ICON_PATH.is_file():
                self.iconbitmap(str(ICON_PATH))
        except Exception:
            pass

    def _apply_rounded_corners(self) -> None:
        """Native rounded corners on Windows 11 (no-op on Windows 10)."""
        if sys.platform != "win32":
            return
        try:
            self.update_idletasks()
            user32 = ctypes.windll.user32
            user32.GetAncestor.restype = ctypes.c_void_p
            hwnd = user32.GetAncestor(ctypes.c_void_p(self.winfo_id()), 2) or self.winfo_id()
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi = ctypes.windll.dwmapi
            dwmapi.DwmSetWindowAttribute.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                33,  # DWMWA_WINDOW_CORNER_PREFERENCE
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
            pass

    def _apply_font_scale(self) -> None:
        for name, font in self._fonts.items():
            font.configure(size=max(6, round(FONTS[name][1] * self._scale)))

    def _startup_position(self, settings: dict) -> tuple[int, int]:
        """Saved position; otherwise top right."""
        try:
            return int(settings["x"]), int(settings["y"])
        except (KeyError, TypeError, ValueError):
            width = round(BASE_W * self._unit)
            margin = round(48 * self._dpi)
            return max(margin, self.winfo_screenwidth() - width - margin), margin

    def _fit_on_screen(self, width: int, height: int, x: int, y: int) -> tuple[int, int]:
        """Moves the window just enough to keep it fully on screen."""
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        return min(max(0, x), max(0, screen_w - width)), min(max(0, y), max(0, screen_h - height))

    def _apply_size(self, width: int, height: int) -> None:
        if self._size == (width, height):
            return
        self._size = (width, height)
        self._pos = self._fit_on_screen(width, height, *self._pos)
        self.geometry(f"{width}x{height}+{self._pos[0]}+{self._pos[1]}")

    def _set_scale(self, scale: float, remember: bool = False) -> None:
        scale = _clamp(scale, MIN_SCALE, MAX_SCALE)
        if abs(scale - self._scale) >= 0.005:
            self._scale = scale
            self._apply_font_scale()
            self._redraw()
        if remember:
            self._remember_geometry()

    def _request_scale(self, scale: float) -> None:
        """Batches scale changes while dragging: at most one per frame."""
        self._pending_scale = scale
        if self._scale_job is None:
            self._scale_job = self.after(16, self._flush_scale)

    def _flush_scale(self) -> None:
        self._scale_job = None
        if self._pending_scale is not None:
            scale, self._pending_scale = self._pending_scale, None
            self._set_scale(scale)

    def _step_scale(self, factor: float) -> None:
        self._set_scale(self._scale * factor, remember=True)

    def _remember_geometry(self) -> None:
        if not self._persist:
            return
        settings = _load_settings()
        for old_key in ("width", "height"):  # format of earlier versions
            settings.pop(old_key, None)
        settings.update({"scale": round(self._scale, 3), "x": self._pos[0], "y": self._pos[1]})
        _save_settings(settings)

    # -- what is shown ------------------------------------------------------

    @staticmethod
    def _next_hot(previous: bool, temperature: Optional[float]) -> bool:
        """Hysteresis: red from 85 °C, no longer red below 80 °C."""
        if temperature is None:
            return False
        if temperature >= ALERT_ON:
            return True
        if temperature < ALERT_OFF:
            return False
        return previous

    def _peak(self, key: str) -> float:
        values = self._history[key]
        return max(values) if values else 0.0

    def _temp_tone(self, key: str, celsius: float, advice: Advice) -> str:
        cause = f"{key}-heat" in advice.causes
        if self._hot[key] or (cause and advice.level == LEVEL_URGENT):
            return COLOR_URGENT
        if celsius >= REST_HEAT_C or cause:
            return COLOR_REST
        return COLOR_CALM

    def _headline(self) -> tuple[str, str, str, str, str]:
        """(level, title, text, title color, light color)."""
        worker = self._worker
        if worker is not None and not worker.is_alive() and worker.error:
            return "stopped", "Sensors stopped", worker.error, COLOR_INK, COLOR_INK_3
        snap = self._snapshot
        if snap is None:
            return "init", "Starting sensors…", "", COLOR_INK_2, COLOR_INK_3
        age = time.time() - snap.stamp if snap.stamp else 0.0
        if age > STALE_S:
            return "stale", f"No readings for {age:.0f} s", "", COLOR_INK_2, COLOR_INK_3
        advice = snap.advice
        if advice.level == LEVEL_URGENT:
            return LEVEL_URGENT, advice.title, advice.text, COLOR_URGENT, COLOR_URGENT
        if advice.level == LEVEL_REST:
            return LEVEL_REST, advice.title, advice.text, COLOR_REST, COLOR_REST
        temps = [t for t in (snap.cpu_temp, snap.gpu_temp) if t is not None]
        if any(self._hot.values()):
            return "heat", "Running hot", "", COLOR_INK, COLOR_URGENT
        if any(t >= REST_HEAT_C for t in temps):
            return "heat", "Running hot", "", COLOR_INK, COLOR_REST
        return LEVEL_OK, "All good", "", COLOR_INK, COLOR_OK

    def _rows(self) -> list[_Row]:
        snap = self._snapshot
        if snap is None:
            return [_Row("cpu", "CPU", "—"), _Row("ram", "RAM", "—"), _Row("gpu", "GPU", "—")]

        advice = snap.advice
        level_color = COLOR_URGENT if advice.level == LEVEL_URGENT else COLOR_REST
        rows = []

        cpu = _Row(
            "cpu", "CPU", f"{snap.cpu_percent:.0f}", "%",
            fraction=snap.cpu_percent / 100.0, peak=self._peak("cpu"),
            tone=level_color if "cpu-load" in advice.causes else COLOR_CALM,
        )
        if snap.cpu_temp is not None:
            tone = self._temp_tone("cpu", snap.cpu_temp, advice)
            cpu.aside = f"{snap.cpu_temp:.0f} °C"
            cpu.aside_color = COLOR_INK_2 if tone == COLOR_CALM else tone
        rows.append(cpu)

        if snap.ram_available_mb is not None and snap.ram_total_mb:
            free = snap.ram_available_mb
            if free >= 1024:
                number, unit = f"{free / 1024:.1f}", "GB free"
            else:
                number, unit = f"{free:.0f}", "MB free"
            rows.append(_Row(
                "ram", "RAM", number, unit,
                fraction=1.0 - free / snap.ram_total_mb, peak=self._peak("ram"),
                tone=level_color if "ram" in advice.causes else COLOR_CALM,
            ))

        if snap.gpu_present:
            if snap.gpu_util_reported:
                gpu = _Row(
                    "gpu", "GPU", f"{snap.gpu_percent:.0f}", "%",
                    fraction=snap.gpu_percent / 100.0, peak=self._peak("gpu"),
                )
                if snap.gpu_temp is not None:
                    tone = self._temp_tone("gpu", snap.gpu_temp, advice)
                    gpu.aside = f"{snap.gpu_temp:.0f} °C"
                    gpu.aside_color = COLOR_INK_2 if tone == COLOR_CALM else tone
            elif snap.gpu_temp is not None:
                # The driver doesn't report usage (e.g. GT 710): measure what is real.
                gpu = _Row(
                    "gpu", "GPU", f"{snap.gpu_temp:.0f}", "°C",
                    fraction=_temp_fraction(snap.gpu_temp), peak=self._peak("gpu"),
                    tone=self._temp_tone("gpu", snap.gpu_temp, advice),
                    danger_from=_temp_fraction(URGENT_HEAT_C),
                )
            else:
                gpu = _Row("gpu", "GPU", "—")
            rows.append(gpu)
        return rows

    # -- drawing ------------------------------------------------------------

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        u = self._unit
        width = round(BASE_W * u)
        pad = round(PAD * u)
        fonts = self._fonts
        level, title, text, title_color, led_color = self._headline()
        banded = level in (LEVEL_REST, LEVEL_URGENT)

        # Headline: measure first (the advice band sits behind the text).
        inset, btn_w, btn_h = round(INSET * u), round(BTN_W * u), round(BTN_H * u)
        close = (width - inset - btn_w, inset, width - inset, inset + btn_h)
        pin = (close[0] - btn_w, inset, close[0], inset + btn_h)
        text_x = pad + round((LED + 8) * u)
        title_h = fonts["status"].metrics("linespace")
        title_lines = _wrap_balanced(title, fonts["status"], pin[0] - round(8 * u) - text_x)
        title_y = inset + (btn_h - title_h) // 2
        body_h = fonts["text"].metrics("linespace")
        body_lines = _wrap(text, fonts["text"], width - pad - text_x) if text else []
        body_y = title_y + len(title_lines) * title_h + round(3 * u)
        head_bottom = max(inset + btn_h, body_y + len(body_lines) * body_h if body_lines else title_y + len(title_lines) * title_h)

        if banded:
            band_bottom = head_bottom + round(10 * u)
            tint, rule = (COLOR_URGENT_TINT, COLOR_URGENT) if level == LEVEL_URGENT else (COLOR_REST_TINT, COLOR_REST)
            canvas.create_rectangle(0, 0, width, band_bottom, fill=tint, outline="", tags=("band",))
            canvas.create_rectangle(0, 0, max(2, round(3 * u)), band_bottom, fill=rule, outline="", tags=("band",))
            y = band_bottom + round(12 * u)
        else:
            y = head_bottom + round(12 * u)

        led = max(4, round(LED * u))
        led_y = title_y + (title_h - led) // 2
        canvas.create_rectangle(pad, led_y, pad + led, led_y + led, fill=led_color, outline="", tags=("led",))
        for index, line in enumerate(title_lines):
            canvas.create_text(text_x, title_y + index * title_h, text=line, anchor="nw",
                               font=fonts["status"], fill=title_color, tags=("status",))
        body_color = COLOR_INK if banded else COLOR_INK_2
        for index, line in enumerate(body_lines):
            canvas.create_text(text_x, body_y + index * body_h, text=line, anchor="nw",
                               font=fonts["text"], fill=body_color, tags=("advice",))
        self._draw_button("pin", pin, ICON_PINNED if self._topmost else ICON_PIN)
        self._draw_button("close", close, ICON_CLOSE)

        # Rows: label and figure share a baseline; the meter sits underneath.
        count, seg, gap = _segments(width - 2 * pad, u)
        meter_right = pad + count * seg + (count - 1) * gap
        seg_h = max(3, round(SEG_H * u))
        value_ascent = fonts["value"].metrics("ascent")
        for row in self._rows():
            baseline = y + value_ascent
            canvas.create_text(pad, baseline - fonts["label"].metrics("ascent"), text=row.label,
                               anchor="nw", font=fonts["label"], fill=COLOR_INK_2, tags=(f"{row.key}-label",))
            right = meter_right
            if row.unit:
                canvas.create_text(right, baseline - fonts["unit"].metrics("ascent"), text=row.unit,
                                   anchor="ne", font=fonts["unit"], fill=COLOR_INK_2, tags=(f"{row.key}-unit",))
                right -= fonts["unit"].measure(row.unit) + round(3 * u)
            canvas.create_text(right, baseline - value_ascent, text=row.number, anchor="ne",
                               font=fonts["value"], fill=COLOR_INK, tags=(f"{row.key}-value",))
            if row.aside:
                aside_right = right - fonts["value"].measure(row.number) - round(10 * u)
                canvas.create_text(aside_right, baseline - fonts["unit"].metrics("ascent"), text=row.aside,
                                   anchor="ne", font=fonts["unit"], fill=row.aside_color,
                                   tags=(f"{row.key}-aside",))
            meter_y = baseline + round(6 * u)
            self._draw_meter(row, pad, meter_y, count, seg, gap, seg_h)
            y = meter_y + seg_h + round(ROW_GAP * u)

        height = y - round(ROW_GAP * u) + pad
        self._draw_grip(width, height)
        canvas.create_rectangle(0, 0, width - 1, height - 1, outline=COLOR_EDGE, tags=("edge",))
        self._hits["pin"], self._hits["close"] = pin, close
        self._apply_size(width, height)

    def _draw_button(self, name: str, box: tuple[int, int, int, int], glyph: str) -> None:
        canvas = self._canvas
        hovered = self._hover == name
        if hovered:
            fill = COLOR_CLOSE_HOVER if name == "close" else COLOR_HOVER
            canvas.create_rectangle(*box, fill=fill, outline="", tags=(f"btn-{name}",))
        if name == "close":
            color = "#ffffff" if hovered else COLOR_INK_2
        else:
            color = COLOR_INK if (hovered or self._topmost) else COLOR_INK_3
        canvas.create_text((box[0] + box[2]) / 2, (box[1] + box[3]) / 2, text=glyph,
                           font=self._fonts["icon"], fill=color, tags=(f"btn-{name}",))

    def _draw_meter(self, row: _Row, x0: int, y0: int, count: int, seg: int, gap: int, height: int) -> None:
        canvas = self._canvas
        lit = int(_clamp(row.fraction) * count + 0.5)
        peak = int(_clamp(row.peak) * count + 0.5) - 1
        danger = None if row.danger_from is None else int(row.danger_from * count)
        for index in range(count):
            x = x0 + index * (seg + gap)
            if index < lit:
                fill, kind = row.tone, "lit"
            elif index == peak:
                fill, kind = COLOR_PEAK, "peak"
            elif danger is not None and index >= danger:
                fill, kind = COLOR_DANGER_TRACK, "zone"
            else:
                fill, kind = COLOR_TRACK, "track"
            canvas.create_rectangle(x, y0, x + seg, y0 + height, fill=fill, outline="",
                                    tags=(f"{row.key}-meter", f"{row.key}-{kind}"))

    def _draw_grip(self, width: int, height: int) -> None:
        u = self._unit
        dot, step = max(2, round(2 * u)), max(3, round(4 * u))
        right, bottom = width - max(3, round(4 * u)), height - max(3, round(4 * u))
        color = COLOR_INK_2 if self._hover == "grip" else COLOR_INK_3
        for col in range(3):
            for row in range(3 - col):
                x, y = right - col * step, bottom - row * step
                self._canvas.create_rectangle(x - dot, y - dot, x, y, fill=color, outline="", tags=("grip",))
        size = round(16 * u)
        self._hits["grip"] = (width - size, height - size, width, height)

    # -- mouse and keyboard -------------------------------------------------

    def _bind_events(self) -> None:
        canvas = self._canvas
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Leave>", self._on_leave)
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<Double-Button-1>", self._on_double)
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<Control-t>", lambda _event: self.toggle_topmost())
        self.bind("<Control-minus>", lambda _event: self._step_scale(1 / 1.1))
        self.bind("<Control-equal>", lambda _event: self._step_scale(1.1))
        self.bind("<Control-plus>", lambda _event: self._step_scale(1.1))
        self.bind("<Control-0>", lambda _event: self._set_scale(1.0, remember=True))

    def _hit(self, x: int, y: int) -> Optional[str]:
        for name, (x0, y0, x1, y1) in self._hits.items():
            if x0 <= x < x1 and y0 <= y < y1:
                return name
        return None

    def _on_motion(self, event) -> None:
        target = self._hit(event.x, event.y)
        if target != self._hover:
            self._hover = target
            self._canvas.configure(cursor="size_nw_se" if target == "grip" else "")
            self._redraw()

    def _on_leave(self, _event) -> None:
        if self._hover is not None and self._press is None:
            self._hover = None
            self._canvas.configure(cursor="")
            self._redraw()

    def _on_press(self, event) -> None:
        self._canvas.focus_set()  # so Esc and the shortcuts reach the panel
        self._press = self._hit(event.x, event.y) or "drag"
        if self._press == "grip":
            self._resize_start(event)
        elif self._press == "drag":
            self._drag_start(event)

    def _on_drag(self, event) -> None:
        if self._press == "drag":
            self._drag_move(event)
        elif self._press == "grip":
            self._resize_move(event)

    def _on_release(self, event) -> None:
        press, self._press = self._press, None
        if press in ("pin", "close") and self._hit(event.x, event.y) == press:
            if press == "close":
                self.close()
                return
            self.toggle_topmost()
        elif press == "grip":
            if self._scale_job is not None:
                self.after_cancel(self._scale_job)
                self._flush_scale()
            self._remember_geometry()
        elif press == "drag":
            self._remember_geometry()

    def _on_double(self, event) -> None:
        if self._hit(event.x, event.y) == "grip":
            self._set_scale(1.0, remember=True)

    def _drag_start(self, event) -> None:
        self._drag_anchor = (event.x_root, event.y_root)
        self._drag_window = (self.winfo_x(), self.winfo_y())

    def _drag_move(self, event) -> None:
        x = self._drag_window[0] + event.x_root - self._drag_anchor[0]
        y = self._drag_window[1] + event.y_root - self._drag_anchor[1]
        self._pos = (x, y)
        self.geometry(f"+{x}+{y}")

    def _resize_start(self, event) -> None:
        self._resize_anchor = (event.x_root, event.y_root)
        width, height = self._size or (round(BASE_W * self._unit), 1)
        self._resize_from = (self._scale, width, height)

    def _resize_move(self, event) -> None:
        """Proportional scaling: the drag is projected onto the panel's diagonal."""
        scale, width, height = self._resize_from
        dx = event.x_root - self._resize_anchor[0]
        dy = event.y_root - self._resize_anchor[1]
        grow = (dx * width + dy * height) / max(1, width * width + height * height)
        self._request_scale(scale * (1.0 + grow))

    # -- lifecycle ----------------------------------------------------------

    def _start_sensors(self) -> None:
        self._worker = SensorWorker(self._queue, SENSOR_INTERVAL)
        self._worker.start()

    def toggle_topmost(self) -> None:
        self._topmost = not self._topmost
        try:
            self.attributes("-topmost", self._topmost)
        except Exception:
            pass
        self._redraw()

    def _poll(self) -> None:
        """Picks up the latest snapshot without blocking the event loop."""
        if self._closing:
            return

        snapshot: Optional[Snapshot] = None
        try:
            snapshot = self._queue.get_nowait()
        except queue.Empty:
            pass

        if snapshot is not None:
            self._render(snapshot)
        else:
            level = self._headline()[0]
            if level in ("stale", "stopped"):
                self._redraw()  # keeps "no readings for N s" up to date

        self.after(POLL_MS, self._poll)

    def _render(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._hot["cpu"] = self._next_hot(self._hot["cpu"], snapshot.cpu_temp)
        self._hot["gpu"] = self._next_hot(self._hot["gpu"], snapshot.gpu_temp)

        self._history["cpu"].append(_clamp(snapshot.cpu_percent / 100.0))
        if snapshot.ram_available_mb is not None and snapshot.ram_total_mb:
            self._history["ram"].append(_clamp(1.0 - snapshot.ram_available_mb / snapshot.ram_total_mb))
        if snapshot.gpu_present:
            if snapshot.gpu_util_reported:
                self._history["gpu"].append(_clamp(snapshot.gpu_percent / 100.0))
            elif snapshot.gpu_temp is not None:
                self._history["gpu"].append(_temp_fraction(snapshot.gpu_temp))
        self._redraw()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._scale_job is not None:
            try:
                self.after_cancel(self._scale_job)
            except Exception:
                pass
        self._remember_geometry()

        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()
            worker.join(timeout=1.5)  # daemon thread: it never blocks exit

        try:
            self.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Console diagnostics
# --------------------------------------------------------------------------

def run_self_test(samples: int, interval: float) -> int:
    """Prints real sensor readings. Opens no window."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print(f"== Telltale {__version__} · self-test ==")
    print(f"Python {platform.python_version()} · {platform.platform()}")
    print(
        f"tkinter {tk.TkVersion} · psutil {psutil.__version__} · "
        f"pynvml {'yes' if pynvml is not None else 'no'} · wmi {'yes' if wmi is not None else 'no'}"
    )

    if sys.platform == "win32":
        try:
            import pythoncom

            pythoncom.CoInitialize()  # needed to query WMI from this thread
        except Exception:
            pass

    hub = SensorHub()
    exit_code = 0
    try:
        print(f"CPU: {hub.cpu_name}")
        for index in range(samples):
            started = time.monotonic()
            snapshot = hub.sample()
            elapsed_ms = (time.monotonic() - started) * 1000.0

            print(
                f"[{index + 1}/{samples}] CPU {snapshot.cpu_percent:5.1f} % · "
                f"temp {_fmt_temp(snapshot.cpu_temp)} (source: {snapshot.cpu_source}) · "
                f"{elapsed_ms:.0f} ms"
            )
            gpu_line = (
                f"            GPU {snapshot.gpu_percent:5.1f} % · "
                f"temp {_fmt_temp(snapshot.gpu_temp)} · "
                f"{snapshot.gpu_name or 'no NVIDIA GPU'}"
            )
            if snapshot.gpu_present and not snapshot.gpu_util_reported:
                gpu_line += " (driver doesn't report usage)"
            print(gpu_line)
            if snapshot.ram_available_mb is not None and snapshot.ram_total_mb:
                print(f"            RAM {_fmt_mb(snapshot.ram_available_mb)} free of {_fmt_mb(snapshot.ram_total_mb)}")
            if snapshot.notes:
                print("            notes: " + "; ".join(snapshot.notes))
            if index < samples - 1:
                time.sleep(interval)

        if hub.thermal.source == "none":
            print("\nNote: no CPU temperature sensor is reachable.")
            print("      Install LibreHardwareMonitor (or OpenHardwareMonitor), run it as")
            print("      administrator and run the self-test again. The panel works without it.")
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        exit_code = 1
    finally:
        hub.close()
        del hub
        gc.collect()  # release the COM objects before the process ends
    return exit_code


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="telltale",
        description="A tiny always-on-top hardware gauge for Windows that stays quiet until your PC needs you.",
    )
    parser.add_argument("--self-test", action="store_true", help="print sensor diagnostics and exit")
    parser.add_argument("--samples", type=int, default=3, help="number of self-test samples (3)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between self-test samples (1.0)")
    parser.add_argument("--no-topmost", action="store_true", help="start without always-on-top")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test(max(1, args.samples), max(0.05, args.interval))

    app = MonitorApp(start_topmost=not args.no_topmost)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
