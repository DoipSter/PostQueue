import os
import sys
from pathlib import Path
# Tests import the app modules from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_sessionfinish(session, exitstatus):
    """On macOS, PySide6's interpreter teardown after the off-screen interface tests can crash with a segmentation
    fault, which would turn a green run into a failed build. The tests are done at this point, so leave with the
    real result instead of letting the interpreter tear Qt down."""
    if sys.platform == 'darwin':
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(int(exitstatus))
