"""Validate user-selected media names before any camera starts saving."""
from dataclasses import dataclass
from pathlib import Path
import re

from .media_formats import format_for_extension


@dataclass(frozen=True)
class SaveTarget:
    key: str
    path: Path
    overwrite: bool = False


def validate_filename(name):
    if not name or name in ('.', '..') or name.endswith((' ', '.')):
        raise ValueError('文件名不能为空，也不能以空格或句点结尾。')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError('文件名不能包含 < > : " / \\ | ? * 等字符。')
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {'COM%d' % n for n in range(1, 10)} | {'LPT%d' % n for n in range(1, 10)}
    if name.split('.')[0].upper() in reserved:
        raise ValueError('该文件名是 Windows 保留名称，请更换。')
    if len(name) > 255:
        raise ValueError('文件名过长，请缩短至 255 个字符以内。')
    return name


def media_path(raw_path, kind, default_suffix=None):
    path = Path(raw_path)
    validate_filename(path.name)
    if path.is_dir():
        raise ValueError('目标是一个文件夹，请选择文件名。')
    if not path.suffix:
        path = path.with_name(path.name + (default_suffix or ('.png' if kind == 'photo' else '.avi')))
    validate_filename(path.name)
    format_for_extension(kind, path.suffix)
    path = path.resolve()
    if path.is_dir():
        raise ValueError('目标是一个文件夹，请选择文件名。')
    return path


def path_identity(path):
    # Windows filenames are normally case-insensitive, including in batch plans.
    return str(Path(path).resolve()).casefold()


def validate_targets(targets, busy_paths=(), shared_directory=None):
    used, busy = set(), {path_identity(path) for path in busy_paths if path}
    shared = path_identity(shared_directory) if shared_directory is not None else None
    for target in targets:
        if shared is not None and path_identity(target.path.parent) != shared:
            raise ValueError('多路同时拍摄的文件必须保存在所选的同一文件夹内。')
        identity = path_identity(target.path)
        if identity in used:
            raise ValueError('不同机位不能使用同一个文件名，请分别命名。')
        if identity in busy:
            raise ValueError('该文件名正被正在录制的机位使用，请选择其他文件名。')
        used.add(identity)
    return targets
