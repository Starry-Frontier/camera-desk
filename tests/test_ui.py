"""Exercise user-visible cross-camera state and recording shutdown."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
from pathlib import Path

import cv2
from PySide6.QtWidgets import QApplication

from multicam.ui import MainWindow


def spin_until(app, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.015)
    assert predicate(), 'Timed out waiting for the desktop operation'


def test_selected_camera_settings_and_recording_shutdown(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    try:
        window.add_demo_pair()
        spin_until(app, lambda: len(window.sessions) == 2 and all(s.ready for s in window.sessions.values()))
        window.select_session('demo:0')
        assert tuple(window.resolution.currentData()) == (1280, 720)
        window.mirror.setChecked(True)
        window.sliders['contrast'][0].setValue(145)
        assert window.sessions['demo:0'].image.mirror
        assert window.sessions['demo:0'].image.contrast == 1.45
        window.select_session('demo:1')
        assert not window.mirror.isChecked()
        assert window.sliders['contrast'][0].value() == 100
        window.select_session('demo:0')
        assert window.mirror.isChecked()
        assert window.sliders['contrast'][0].value() == 145
        window.layout_combo.setCurrentIndex(3)
        assert not window.sessions['demo:1'].tile.isVisible()
        window.select_session('demo:1')
        assert window.sessions['demo:1'].tile.isVisible()
        window.snapshot_all(prompt=False)
        try:
            spin_until(app, lambda: len(list(window.output_dir.glob('*.png'))) == 2)
        except AssertionError:
            raise AssertionError(window.notice.text())
        window.record_all(prompt=False)
        spin_until(app, lambda: all(s.recording for s in window.sessions.values()))
        assert not window.apply_btn.isEnabled()
        assert not window.all_record.isEnabled()
        assert window.all_stop.isEnabled()
        started = time.monotonic()
        spin_until(app, lambda: time.monotonic() - started > 1.2)
        window.remove_camera('demo:0')
        spin_until(app, lambda: 'demo:0' not in window.sessions)
        assert window.sessions['demo:1'].recording
        window.close()
        spin_until(app, lambda: not window.isVisible())
        videos = list(window.output_dir.glob('*.avi'))
        assert len(videos) == 2
        for path in videos:
            capture = cv2.VideoCapture(str(path))
            assert capture.isOpened()
            assert capture.get(cv2.CAP_PROP_FRAME_COUNT) > 10
            capture.release()
        moved = tmp_path / 'moved-portable-data'
        moved.mkdir()
        (moved / 'settings.json').write_text((tmp_path / 'settings.json').read_text(encoding='utf-8'), encoding='utf-8')
        restored = MainWindow(moved)
        assert restored.output_dir == moved / 'captures'
        restored.show()
        restored.add_demo_pair()
        assert restored.sessions['demo:0'].image.mirror
        assert not restored.sessions['demo:1'].image.mirror
        restored.close()
        spin_until(app, lambda: not restored.isVisible())
        app.processEvents()
    finally:
        window.close()
        spin_until(app, lambda: all(not s.worker.isRunning() for s in window.sessions.values()))
