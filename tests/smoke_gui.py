"""GUI smoke test: opens the panel and checks drawing, advice and scaling.

Usage:  python tests/smoke_gui.py
"""
import ctypes
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telltale  # noqa: E402

checks = []


def window_icon_handles(app):
    """Icon handles Windows actually assigned to the window (WM_GETICON)."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = ctypes.c_void_p
    hwnd = user32.GetAncestor(ctypes.c_void_p(app.winfo_id()), 2)
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_void_p
    small = user32.SendMessageW(ctypes.c_void_p(hwnd), 0x007F, ctypes.c_void_p(0), ctypes.c_void_p(0))
    big = user32.SendMessageW(ctypes.c_void_p(hwnd), 0x007F, ctypes.c_void_p(1), ctypes.c_void_p(0))
    return small, big


def check(name, condition, detail=""):
    checks.append((name, bool(condition)))
    print(("PASS " if condition else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def texts(app, tag):
    return [app._canvas.itemcget(item, "text") for item in app._canvas.find_withtag(tag)]


def fills(app, tag):
    return {app._canvas.itemcget(item, "fill") for item in app._canvas.find_withtag(tag)}


def snap(**values):
    base = dict(cpu_percent=30.0, cpu_temp=None, gpu_percent=0.0, gpu_temp=45.0, gpu_present=True,
                gpu_util_reported=False, gpu_name="Test GPU", ram_available_mb=4000.0, ram_total_mb=16000.0,
                stamp=time.time())
    base.update(values)
    return telltale.Snapshot(**base)


def event(**values):
    return type("Event", (), values)()


def main():
    app = telltale.MonitorApp(start_topmost=False, persist=False)

    def stage_real():
        check("window is visible", app.winfo_viewable() == 1)
        try:
            small, big = window_icon_handles(app)
            check("window icon loaded (WM_GETICON)", bool(small) and bool(big), f"small={small} big={big}")
        except Exception as exc:
            check("window icon loaded (WM_GETICON)", False, f"{type(exc).__name__}: {exc}")
        expected_w = round(telltale.BASE_W * app._unit)
        check("reference width", app.winfo_width() == expected_w, f"{app.winfo_width()} vs {expected_w}")
        check("height fits the content", 120 <= app.winfo_height() <= 320, str(app.winfo_height()))
        status = " ".join(texts(app, "status"))
        check("headline has text", len(status) > 3, status)
        cpu = texts(app, "cpu-value")
        check("CPU shows its figure", bool(cpu) and cpu[0].isdigit(), cpu)
        ram = texts(app, "ram-unit")
        check("RAM shows GB or MB free", bool(ram) and ram[0].endswith("free"), ram)
        print(f"      live -> {status} | CPU {cpu} | RAM {texts(app, 'ram-value')} {ram} | "
              f"GPU {texts(app, 'gpu-value')} {texts(app, 'gpu-unit')}")

        app.update_idletasks()
        start_x, start_y = app.winfo_x(), app.winfo_y()
        app._drag_start(event(x_root=500, y_root=300))
        app._drag_move(event(x_root=537, y_root=321))
        app.update_idletasks()
        check("dragging moves the window",
              (app.winfo_x(), app.winfo_y()) == (start_x + 37, start_y + 21),
              f"{(app.winfo_x(), app.winfo_y())} from {(start_x, start_y)}")
        app.toggle_topmost()
        check("pin turns always-on-top on", bool(app.attributes("-topmost")) is True)
        app.toggle_topmost()
        check("pin turns it back off", bool(app.attributes("-topmost")) is False)

        app._worker.stop()
        time.sleep(0.2)
        while True:
            try:
                app._queue.get_nowait()
            except Exception:
                break
        app.after(50, stage_states)

    def stage_states():
        app._render(snap())
        check("normal: \"All good\"", texts(app, "status") == ["All good"], texts(app, "status"))
        check("normal: no advice band", not app._canvas.find_withtag("band"))
        check("normal: GPU meter is calm", fills(app, "gpu-lit") == {telltale.COLOR_CALM}, fills(app, "gpu-lit"))
        check("temperature scale marks the danger zone", bool(app._canvas.find_withtag("gpu-zone")))
        app.update()
        calm_height = app.winfo_height()

        app._render(snap(gpu_temp=91.0))
        check("91 °C: meter turns red", fills(app, "gpu-lit") == {telltale.COLOR_URGENT}, fills(app, "gpu-lit"))
        check("91 °C: headline \"Running hot\"", texts(app, "status") == ["Running hot"])
        app._render(snap(gpu_temp=82.0))
        check("hysteresis: 82 °C stays red", fills(app, "gpu-lit") == {telltale.COLOR_URGENT})
        app._render(snap(gpu_temp=79.0))
        check("hysteresis: 79 °C back to calm", fills(app, "gpu-lit") == {telltale.COLOR_CALM})
        app._render(snap(gpu_temp=82.0))
        check("82 °C without coming from red: amber", fills(app, "gpu-lit") == {telltale.COLOR_REST})

        app._render(snap(ram_available_mb=250.0, advice=telltale.Advice(
            telltale.LEVEL_URGENT, "Urgent: close or throttle apps",
            "Only 250 MB of RAM left. Top RAM users: chrome 1.8 GB.", ("ram",))))
        check("urgent: band is shown", bool(app._canvas.find_withtag("band")))
        check("urgent: title fits on one line", texts(app, "status") == ["Urgent: close or throttle apps"],
              texts(app, "status"))
        check("urgent: advice text", "250 MB" in " ".join(texts(app, "advice")), texts(app, "advice"))
        check("urgent: RAM meter is red", fills(app, "ram-lit") == {telltale.COLOR_URGENT}, fills(app, "ram-lit"))
        check("urgent: \"MB free\" under 1 GB", texts(app, "ram-unit") == ["MB free"], texts(app, "ram-unit"))

        app._render(snap(cpu_percent=95.0, advice=telltale.Advice(
            telltale.LEVEL_REST, "Give your PC a break",
            "The CPU has been at 95% for over a minute. It's fine, in theory.", ("cpu-load",))))
        check("break: title fits on one line", texts(app, "status") == ["Give your PC a break"],
              texts(app, "status"))
        check("break: CPU meter is amber", fills(app, "cpu-lit") == {telltale.COLOR_REST}, fills(app, "cpu-lit"))
        app.update()
        check("with advice the panel grows to fit it", app.winfo_height() > calm_height,
              f"{app.winfo_height()} vs {calm_height}")

        app._render(snap())
        app.update()
        check("without advice the band disappears", not app._canvas.find_withtag("band"))
        check("no \"recovered\" message", texts(app, "advice") == [] and texts(app, "status") == ["All good"])
        check("back to the resting height", app.winfo_height() == calm_height,
              f"{app.winfo_height()} vs {calm_height}")

        app._render(snap(gpu_present=False, gpu_temp=None))
        check("no NVIDIA GPU: no GPU row", not app._canvas.find_withtag("gpu-meter"))
        app._render(snap(gpu_util_reported=True, gpu_percent=40.0, gpu_temp=50.0))
        check("driver reports usage: GPU shows %",
              texts(app, "gpu-unit") == ["%"] and texts(app, "gpu-aside") == ["50 °C"],
              (texts(app, "gpu-unit"), texts(app, "gpu-aside")))
        app._render(snap(stamp=time.time() - 10))
        check("stale readings: the headline says so", texts(app, "status")[0].startswith("No readings"),
              texts(app, "status"))
        app.after(50, stage_scale)

    def stage_scale():
        app._render(snap())
        app.update()
        w0, h0 = app.winfo_width(), app.winfo_height()
        app._set_scale(1.4)
        app.update()
        w1, h1 = app.winfo_width(), app.winfo_height()
        check("scaling grows width and height proportionally",
              abs(w1 - round(w0 * 1.4)) <= 2 and abs(h1 / h0 - 1.4) < 0.08, f"{w0}x{h0} -> {w1}x{h1}")
        check("fonts scale too", int(app._fonts["value"].cget("size")) == round(17 * 1.4),
              app._fonts["value"].cget("size"))
        app._set_scale(0.1)
        app.update()
        check("minimum scale is respected", abs(app._scale - telltale.MIN_SCALE) < 1e-9, app._scale)

        app._set_scale(1.0)
        app.update()
        x0, y0, x1, y1 = app._hits["grip"]
        gx, gy = (x0 + x1) // 2, (y0 + y1) // 2
        app._on_press(event(x=gx, y=gy, x_root=1000, y_root=600))
        app._on_drag(event(x=gx + 60, y=gy + 40, x_root=1060, y_root=640))
        app._on_release(event(x=gx + 60, y=gy + 40, x_root=1060, y_root=640))
        app.update()
        check("dragging the grip enlarges the panel", app._scale > 1.1, f"scale {app._scale:.2f}")

        x0, y0, x1, y1 = app._hits["pin"]
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        app._on_press(event(x=cx, y=cy, x_root=cx, y_root=cy))
        app._on_release(event(x=cx, y=cy, x_root=cx, y_root=cy))
        check("pin button toggles always-on-top", bool(app.attributes("-topmost")) is True)
        app.after(200, app.close)

    app.after(2200, stage_real)
    app.mainloop()
    failed = sum(1 for _, ok in checks if not ok)
    print(f"RESULT: {len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
