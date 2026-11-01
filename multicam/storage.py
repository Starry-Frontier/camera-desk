"""Broad planning ranges; variable scene complexity prevents exact prediction."""
import math
import shutil
from pathlib import Path


def estimate_storage(configs, duration, suffix, interval=None):
    duration = max(0.0, float(duration))
    low = high = 0.0
    count = 0
    for config in configs:
        pixels = config.width * config.height
        if interval is None:
            # AVI/MKV use intra-frame MJPEG; MP4 uses inter-frame MPEG-4.
            a, b = (.005, .15) if suffix == '.mp4' else (.025, .65)
            low += pixels * config.fps * duration * a
            high += pixels * config.fps * duration * b
        else:
            shots = int(math.ceil(duration / float(interval))) if duration else 0
            count += shots
            a, b = {'.bmp': (3, 3.1), '.tif': (.1, 3.2), '.png': (.05, 3.2),
                    '.jpg': (.025, .9), '.webp': (.01, .9)}[suffix]
            low += shots * pixels * a
            high += shots * pixels * b
    return {'minimum_bytes': int(low), 'maximum_bytes': int(high), 'photos': count}


def format_bytes(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return '%.1f %s' % (value, unit)
        value /= 1024


def free_space(directory):
    path = Path(directory).resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None
