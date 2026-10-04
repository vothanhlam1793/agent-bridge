"""Writable application data, separate from bundled resources."""
import os
import sys
from pathlib import Path

def data_dir():
    override = os.getenv("PM_BRIDGE_DATA_DIR")
    if override:
        path = Path(override)
    elif getattr(sys, "frozen", False):
        path = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "PMAssistantBridge"
    else:
        path = Path(__file__).resolve().parent
    path.mkdir(parents=True, exist_ok=True)
    return path
