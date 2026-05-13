# Antrack

Antrack is a Python desktop application for antenna tracking, pointing, and radio-noise scan workflows.

The application is built around:
- `PyQt5` for the user interface
- `qasync` for Qt/async integration
- `Skyfield` / SPICE-based ephemeris calculations
- Axis motion control and SDR-based measurements

## Main Features

- Track solar-system objects, stars, radio sources, and satellites
- Control antenna azimuth and elevation
- Run fixed positioning and relative scan sequences
- Measure radio band power or SNR through the SDR backend
- Visualize scan grids, offsets, and tracking error evolution

## Repository Layout

```text
Antrack/
├── pyproject.toml         # Project metadata and dependencies
├── settings.txt           # Local application configuration
├── src/
│   ├── antrack/           # Main application package
│   │   ├── core/          # Hardware I/O, SDR, Axis, DSP
│   │   ├── gui/           # Qt user interface
│   │   ├── tracking/      # Tracking and scan logic
│   │   ├── threading_utils/
│   │   └── utils/
│   └── data/              # Ephemeris files, TLEs, catalogs
├── tests/                 # Pytest test suite
├── logs/                  # Runtime logs
└── docs/                  # Additional documentation
```

## Requirements

- Windows
- Python `3.12.x`
- Git

Recommended before first use:
- install the drivers needed by your SDR
- ensure the antenna controller is reachable from the PC

## Clone and Install on a New PC

Open PowerShell and run:

```powershell
git clone https://github.com/stephanerey/Antrack.git
cd Antrack
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If `py -3.12` is not available, install Python 3.12 first and make sure it is accessible from PowerShell.

## Run the Application

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m antrack.main
```

or, once the editable install is complete:

```powershell
antrack
```

## Run the Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Configuration

The application uses `settings.txt` from the repository root by default.

You can override the configuration path with:

```powershell
$env:ANTRACK_CONFIG_PATH = "C:\path\to\settings.txt"
antrack
```

Typical configuration areas include:
- observer coordinates
- antenna limits and motion speeds
- network settings for the Axis controller
- scan offsets and tracking thresholds

## Data Files

The repository contains runtime data under `src/data/`, including:
- planetary ephemeris files
- TLE files
- source and catalog CSV files

These files should not be overwritten casually on a production setup.

## Typical First Start Checklist

1. Clone the repository and install dependencies.
2. Review `settings.txt` for the target PC and hardware setup.
3. Start the application.
4. Connect to the Axis controller.
5. Verify live antenna telemetry.
6. Start the SDR if needed.
7. Select a target and begin tracking or scanning.

## Development Notes

- Internal imports are absolute under `antrack`
- Tests use `pytest`
- The main entry point is `antrack.main:main`

## License

No license file is currently declared in the repository.
