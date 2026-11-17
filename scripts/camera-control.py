"""Start the control client from any working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multicam.control_client import main


if __name__ == '__main__':
    sys.exit(main())
