"""Paths shared by the research scripts in work/.

Scripts run as ``python work/<script>.py`` from the repository root, so this
module is importable as ``workspace``.
"""

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORK_DIRECTORY = REPOSITORY_ROOT / "work"
PYTHON_PACKAGE_DIRECTORY = REPOSITORY_ROOT / "python"
REFERENCE_DIRECTORY = WORK_DIRECTORY / "reference"
VENDOR_DIRECTORY = WORK_DIRECTORY / "vendor"
TOOLS_DIRECTORY = WORK_DIRECTORY / "tools"
VALIDATION_DIRECTORY = REPOSITORY_ROOT / "docs" / "validation"
PROFILES_DIRECTORY = REPOSITORY_ROOT / "profiles"
INSTALLED_CLIENT_PATH = Path(r"C:\ProgramData\Jagex\launcher\rs2client.exe")
PRIMARY_CLIENT_SHA256 = "fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45"


def add_import_paths(*, package=False, reference=False, vendor=False):
    """Prepend the rs3_reader package, the reference decoder or vendored tools to sys.path."""
    for enabled, directory in (
        (vendor, VENDOR_DIRECTORY),
        (reference, REFERENCE_DIRECTORY),
        (package, PYTHON_PACKAGE_DIRECTORY),
    ):
        if enabled and str(directory) not in sys.path:
            sys.path.insert(0, str(directory))


def first_client_pid():
    """Return the first rs2client.exe PID; requires add_import_paths(reference=True)."""
    from reference_memory import processes

    client_pids = list(processes())
    if not client_pids:
        raise RuntimeError("No RS3 client is running")
    return client_pids[0]
