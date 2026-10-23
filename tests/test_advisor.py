"""Tests for the alert rules and pure helpers: no window, no real sensors.

Usage:  python tests/test_advisor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telltale  # noqa: E402

OK, REST, URGENT = telltale.LEVEL_OK, telltale.LEVEL_REST, telltale.LEVEL_URGENT
checks = []


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def no_top(by):
    return ""


def feed(advisor, t, top=no_top, cpu=20.0, ram=4000.0, cpu_temp=None, gpu_temp=None):
    """One snapshot at second t with the given values (everything else normal)."""
    snap = telltale.Snapshot(cpu_percent=cpu, cpu_temp=cpu_temp, gpu_temp=gpu_temp,
                             ram_available_mb=ram, ram_total_mb=16000.0, stamp=float(t))
    return advisor.update(snap, top)


# Break advice for heat: only after 30 s in a row.
adv = telltale.Advisor()
levels = [feed(adv, t, gpu_temp=82.0).level for t in range(0, 31)]
check("heat: nothing before 30 s", all(lv == OK for lv in levels[:30]))
check("heat: suggests a break at 30 s", levels[30] == REST, levels[30])
advice = feed(adv, 31, gpu_temp=82.0)
check("heat: title asks for a break", advice.title == "Give your PC a break", advice.title)
check("heat: names the GPU and the temperature", "GPU" in advice.text and "82 °C" in advice.text, advice.text)
check("heat: \"It's fine, in theory.\"", "It's fine, in theory." in advice.text, advice.text)
check("heat: cause gpu-heat", advice.causes == ("gpu-heat",), advice.causes)
check("hysteresis: 78 °C keeps the break advice", feed(adv, 32, gpu_temp=78.0).level == REST)
check("hysteresis: 75 °C back to normal", feed(adv, 33, gpu_temp=75.0).level == OK)

# A short spike is not enough.
adv = telltale.Advisor()
for t in range(0, 21):
    feed(adv, t, gpu_temp=82.0)
feed(adv, 21, gpu_temp=60.0)  # breaks the streak
check("short spike: 25 s of heat after a break in the streak stay quiet", all(
    feed(adv, t, gpu_temp=82.0).level == OK for t in range(22, 47)))

# Sustained CPU load: 60 s.
adv = telltale.Advisor()
levels = [feed(adv, t, cpu=95.0).level for t in range(0, 61)]
check("load: nothing before 60 s", all(lv == OK for lv in levels[:60]))
check("load: suggests a break at 60 s", levels[60] == REST, levels[60])
advice = feed(adv, 61, cpu=95.0)
check("load: names the CPU", "CPU" in advice.text and "95%" in advice.text, advice.text)
check("load: cause cpu-load", advice.causes == ("cpu-load",), advice.causes)

# Urgent heat: 5 s in a row and the top CPU processes.
calls = []


def record_cpu(by):
    calls.append(by)
    return "chrome 42%, code 12%"


adv = telltale.Advisor()
levels = [feed(adv, t, top=record_cpu, gpu_temp=93.0) for t in range(0, 6)]
check("urgent heat: nothing before 5 s", levels[4].level == OK, levels[4].level)
check("urgent heat: urgent at 5 s", levels[5].level == URGENT, levels[5].level)
check("urgent heat: title asks to close or throttle", levels[5].title == "Urgent: close or throttle apps")
check("urgent heat: text names the GPU and 93 °C", "GPU" in levels[5].text and "93 °C" in levels[5].text,
      levels[5].text)
check("urgent heat: asks for CPU processes", calls and calls[-1] == "cpu")
check("urgent heat: lists the processes", "chrome 42%" in levels[5].text, levels[5].text)
check("urgent heat: cause gpu-heat", levels[5].causes == ("gpu-heat",), levels[5].causes)

# CPU hotter than the GPU: the advice names the CPU.
adv = telltale.Advisor()
levels = [feed(adv, t, cpu_temp=95.0, gpu_temp=60.0) for t in range(0, 6)]
check("urgent CPU heat: names the CPU", levels[5].text.startswith("The CPU is at 95 °C"), levels[5].text)

# Critical RAM: 5 s and the top RAM processes; hysteresis up to 360 MB.
calls = []
adv = telltale.Advisor()
levels = [feed(adv, t, top=lambda by: calls.append(by) or "chrome 1.8 GB", ram=250.0) for t in range(0, 6)]
check("urgent RAM: urgent at 5 s", levels[5].level == URGENT, levels[5].level)
check("urgent RAM: asks for RAM processes", calls and calls[-1] == "ram")
check("urgent RAM: \"Only 250 MB of RAM left.\"", "Only 250 MB of RAM left." in levels[5].text, levels[5].text)
check("urgent RAM: cause ram", levels[5].causes == ("ram",), levels[5].causes)
check("urgent RAM: 320 MB stays urgent (hysteresis)", feed(adv, 6, ram=320.0).level == URGENT)
check("urgent RAM: 370 MB clears it", feed(adv, 7, ram=370.0).level == OK)

# Urgent wins over the break advice, and clearing it leaves no message behind.
adv = telltale.Advisor()
for t in range(0, 31):
    feed(adv, t, gpu_temp=82.0)
levels = [feed(adv, t, gpu_temp=93.0).level for t in range(31, 37)]
check("urgent wins over the break advice", levels[-1] == URGENT, levels)
back = feed(adv, 37, gpu_temp=60.0)
check("no \"recovered\" message when it clears", back == telltale.Advice(), back)

# No reading (N/A) never raises heat advice.
adv = telltale.Advisor()
check("no temperature: never warns about heat", all(feed(adv, t).level == OK for t in range(0, 120)))

# With everything normal, processes are never queried (zero cost at rest).
calls = []
adv = telltale.Advisor()
for t in range(0, 20):
    feed(adv, t, top=lambda by: calls.append(by) or "x", gpu_temp=50.0)
check("at rest: processes are not queried", calls == [], calls)

# Pure helpers.
check("MB and GB formatting", telltale._fmt_mb(250) == "250 MB" and telltale._fmt_mb(1536) == "1.5 GB",
      (telltale._fmt_mb(250), telltale._fmt_mb(1536)))
check("temperature scale 30–100 °C", telltale._temp_fraction(30) == 0.0 and telltale._temp_fraction(100) == 1.0
      and abs(telltale._temp_fraction(65) - 0.5) < 1e-9)
fits = True
for width in range(150, 700, 7):
    for unit in (0.75, 1.0, 1.4, 2.0):
        count, seg, gap = telltale._segments(width, unit)
        used = count * seg + (count - 1) * gap
        fits = fits and used <= width and width - used < count and seg >= 2
check("segments fill the width without overflow or a visible gap", fits)

failed = sum(1 for _, ok in checks if not ok)
print(f"RESULT: {len(checks) - failed}/{len(checks)} checks passed")
raise SystemExit(1 if failed else 0)
