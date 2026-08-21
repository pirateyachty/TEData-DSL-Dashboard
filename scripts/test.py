#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CALLER_SCRIPT = SCRIPT_DIR / "caller_script.py"

selection = input(
    "Please enter account/s "
    "(e.g. 1-10, 19-11, 0227363962, Apt 82): "
).strip()

if not selection:
    print("No account selection entered.")
    sys.exit(1)

print(f"\nRunning caller_script.py {selection}\n")

try:
    subprocess.run(
        [sys.executable, str(CALLER_SCRIPT), selection],
        check=True,
    )
except subprocess.CalledProcessError as exc:
    sys.exit(exc.returncode)
except KeyboardInterrupt:
    print("\nStopped.")
    sys.exit(130)
