# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-10-23

### Added

- Always-on-top instrument panel drawn on a single tkinter Canvas: a headline plus three segmented meters (CPU, RAM, GPU) with a 60-second peak marker.
- Two-level advice: "Give your PC a break" (sustained heat at or above 80 °C for 30 s, or CPU at or above 90% for 60 s) and "Urgent: close or throttle apps" (at or above 90 °C for 5 s, or 300 MB or less of free RAM for 5 s), with hysteresis and no "recovered" noise.
- Lists the top two RAM or CPU consumers during urgent alerts.
- Shows GPU temperature when the driver doesn't report utilization, and hides sensors that can't be read.
- Proportional resize from the corner grip or Ctrl+Minus / Ctrl+Plus / Ctrl+0; scale and position remembered in `%APPDATA%\Telltale\settings.json`.
- Pin (always on top) and close buttons; drag the panel from anywhere.
- `--self-test` sensor diagnostics, plus test suites for the alert rules and the GUI.
- Windows shortcuts installer and standalone `.exe` builds on release.

[Unreleased]: https://github.com/barraco002/telltale/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/barraco002/telltale/releases/tag/v1.0.0
