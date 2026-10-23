# Contributing to Telltale

Thanks for stopping by. Telltale is a tiny always-on-top hardware gauge for Windows that stays quiet until your PC needs you. It is a small project, so a clear bug report, a tested pull request or a better sentence in the docs all make a real difference.

## Ways to contribute

- **Report bugs.** Odd readings, a window that misbehaves, a crash on start: open an issue with the bug report form.
- **Add sensor support for more hardware.** CPUs and GPUs report temperature and utilization in different ways, and Telltale should handle as many as it can without guessing.
- **Translations.** The interface strings are plain Python; a language that keeps the panel readable is welcome.
- **Docs.** Explain a sensor, a threshold or a Windows quirk better than the current text does.

## Development setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Run the app with `python telltale.py`. For sensor diagnostics use `python telltale.py --self-test`: it prints what each sensor reports on your machine and is the fastest way to see whether a problem is in the readings or in the drawing.

## Running the tests

```powershell
python tests/test_advisor.py   # alert rules, no window
python tests/smoke_gui.py      # opens the panel for a few seconds
```

Everything at once:

```powershell
powershell -ExecutionPolicy Bypass -File .\verify.ps1
```

## Design principles

Please keep these in mind before writing code. They are the reason the app is what it is.

1. **Quiet by default.** No new alert unless the PC genuinely needs action. Never auto-close or kill anything; Telltale only advises.
2. **Show only real data.** If a sensor can't be read, hide it instead of showing fake zeros.
3. **No heavy dependencies.** No Qt, no Electron, no web views. Keep it a single file.
4. **Thresholds are constants.** Alert thresholds live at the top of `telltale.py` (`REST_*`, `URGENT_*`, `ALERT_*`), and any change to them needs a test in `tests/test_advisor.py`.

Code style: PEP 8, type hints, small focused functions, and comments that explain *why* rather than *what*.

## Pull request checklist

- [ ] `verify.ps1` passes.
- [ ] New or changed thresholds are covered in `tests/test_advisor.py`.
- [ ] UI changes include a screenshot.
- [ ] One topic per pull request.
- [ ] `CHANGELOG.md` updated under "Unreleased".

## Reporting bugs

Use the issue forms (bug report or feature request). For a bug, please include the output of:

```powershell
python telltale.py --self-test
```

It tells us which sensors were detected and what they returned, which usually shortens the conversation a lot.
