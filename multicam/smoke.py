"""Automated checks for the actual shipped binary, without file dialogs."""
import json
import os
from pathlib import Path
import sys
import time

import cv2
import numpy
import PySide6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from . import __version__
from .save_dialog import BatchSaveDialog


def _loaded_windows_modules():
    if os.name != 'nt':
        return []
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    modules = (wintypes.HMODULE * 2048)()
    needed = wintypes.DWORD()
    psapi = ctypes.windll.psapi
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    if not psapi.EnumProcessModules(kernel.GetCurrentProcess(), modules, ctypes.sizeof(modules), ctypes.byref(needed)):
        raise OSError('Could not inspect bundled runtime libraries')
    if needed.value > ctypes.sizeof(modules):
        raise OSError('Runtime module list exceeded the verification buffer')
    result = []
    for index in range(needed.value // ctypes.sizeof(wintypes.HMODULE)):
        name = ctypes.create_unicode_buffer(32768)
        if kernel.GetModuleFileNameW(modules[index], name, len(name)):
            result.append(name.value)
    return result


def schedule_smoke_test(window, data, formats=False, channels=2):
    evidence = {'version': __version__, 'channels': channels, 'errors': [],
                'recording_events': [], 'photos': [], 'unexpected_dialogs': [],
                'frozen': bool(getattr(sys, 'frozen', False)),
                'executable': sys.executable, 'resource_directory': getattr(sys, '_MEIPASS', ''),
                'module_paths': {name: module.__file__ for name, module in (
                    ('PySide6', PySide6), ('opencv', cv2), ('numpy', numpy))}}

    def no_dialog(*args, **kwargs):
        evidence['unexpected_dialogs'].append('Unexpected save/confirmation dialog')
        return '', ''

    QFileDialog.getSaveFileName = no_dialog
    BatchSaveDialog.exec = lambda *args: evidence['unexpected_dialogs'].append('Unexpected batch dialog') or 0
    QMessageBox.question = lambda *args, **kwargs: evidence['unexpected_dialogs'].append('Unexpected confirmation') or QMessageBox.No

    def write_evidence():
        evidence['loaded_windows_modules'] = _loaded_windows_modules()
        (Path(data) / 'packaged-runtime.json').write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')

    QApplication.instance().aboutToQuit.connect(write_evidence)

    def observe():
        for key, session in window.sessions.items():
            session.worker.error.connect(lambda text, key=key: evidence['errors'].append({'channel': key, 'message': text}))
            session.worker.snapshot_saved.connect(evidence['photos'].append)
            session.worker.recording_changed.connect(lambda active, path, key=key: evidence['recording_events'].append(
                {'channel': key, 'active': active, 'path': path, 'time': time.monotonic()}))

    def prepare():
        if len(window.sessions) != channels or not all(s.ready for s in window.sessions.values()):
            QTimer.singleShot(100, prepare)
            return
        observe()
        if not formats:
            window.record_all(prompt=False)
            QTimer.singleShot(1000, window.all_photo.click)
            QTimer.singleShot(3500, window.stop_all_recordings)
            QTimer.singleShot(4300, lambda: window.grab().save(str(data / 'app-preview.png')))
            QTimer.singleShot(4800, window.close)
            return
        for s in window.sessions.values():
            s.worker.set_format(640, 480, 20)
        QTimer.singleShot(300, begin)

    def photo(suffix, key=None):
        window.remember_media_format('photo', suffix)
        if key is None:
            window.all_photo.click()
        else:
            window.sessions[key].tile.photo_btn.click()

    def begin():
        if not all(s.stats.get('width') == 640 and s.stats.get('height') == 480 for s in window.sessions.values()):
            QTimer.singleShot(100, begin)
            return
        window.remember_media_format('video', '.mp4')
        window.toggle_record('demo:0', prompt=False)
        window.remember_media_format('video', '.mkv')
        window.toggle_record('demo:1', prompt=False)
        QTimer.singleShot(500, lambda: photo('.jpg', 'demo:0'))
        QTimer.singleShot(800, lambda: photo('.bmp', 'demo:1'))
        QTimer.singleShot(1100, lambda: photo('.tif'))
        QTimer.singleShot(1400, lambda: photo('.webp', 'demo:0'))
        QTimer.singleShot(3000, window.stop_all_recordings)
        QTimer.singleShot(3800, lambda: window.grab().save(str(data / 'app-preview.png')))
        QTimer.singleShot(4300, window.close)

    QTimer.singleShot(600, prepare)
    QTimer.singleShot(25000, window.close)
