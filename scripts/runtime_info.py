"""Record the actual bundled runtime and preserve its Python license."""
import json
import platform
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from multicam import __version__

import cv2
import numpy
import PySide6
from PySide6.QtCore import qVersion

bundle = Path(sys.argv[1])
inventory = {
    'application': 'Camera Desk', 'version': __version__,
    'architecture': platform.machine(),
    'python': platform.python_version(), 'pyside6': PySide6.__version__,
    'qt': qVersion(), 'opencv': cv2.__version__, 'numpy': numpy.__version__,
}
(bundle / 'runtime-info.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.exists():
    shutil.copyfile(str(python_license), str(bundle / 'LICENSES' / 'CPython-LICENSE.txt'))
