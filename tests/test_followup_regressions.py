"""Reproduce capture, shutdown and preview races without physical cameras."""
from threading import Event

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

import multicam.capture as backend
from multicam.capture import CameraConfig, CameraWorker
from multicam.saving import SaveTarget
from multicam.ui import MainWindow
from test_audit_regressions import app, wait_for, window


def test_timed_recording_keeps_save_failure_visible(app, window, tmp_path):
    session = window.sessions['demo:0']
    target = tmp_path / 'occupied.avi'
    assert window._start_record_one(session, SaveTarget(session.key, target), duration_seconds=.7)
    wait_for(app, lambda: session.recording)
    target.mkdir()  # Occupy the path after the recording has started.
    wait_for(app, lambda: not session.recording)
    assert target.is_dir()
    assert list(tmp_path.glob('.recording-*.avi'))
    assert '录像保存失败' in window.notice.text()
    assert '录像已保存' not in window.notice.text()


def test_photo_target_stays_busy_across_cameras_until_write_finishes(app, window, tmp_path, monkeypatch):
    first_started, release_first = Event(), Event()
    encode = cv2.imencode

    def demo_frame(width, height, camera, *_):
        return np.full((height, width, 3), 20 if camera == 0 else 220, np.uint8)

    def slow_encode(extension, frame, *args):
        if frame[0, 0, 0] == 20:
            first_started.set()
            release_first.wait(3)
        return encode(extension, frame, *args)

    monkeypatch.setattr(backend, '_demo_frame', demo_frame)
    monkeypatch.setattr(backend.cv2, 'imencode', slow_encode)
    window.prefs['cameras']['demo:1'] = {'config': {'width': 320, 'height': 240, 'fps': 20}}
    window.add_demo_cameras(2)
    first, second = (window.sessions[key] for key in ('demo:0', 'demo:1'))
    wait_for(app, lambda: first.tile.view.image.pixelColor(0, 0).blue() == 20 and second.ready)
    target, independent = tmp_path / 'same.png', tmp_path / 'independent.png'
    target.write_bytes(b'previous file')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'PNG 图片 (*.png)'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    saved = []
    for session in (first, second):
        session.worker.snapshot_saved.connect(saved.append)
    try:
        window.snapshot_one(first.key)
        wait_for(app, first_started.is_set)
        with pytest.raises(ValueError, match='同名照片正在保存'):
            window._confirm_save_targets([SaveTarget(second.key, target)])
        assert not second.worker.take_snapshot(target, overwrite=True)
        assert second.worker.take_snapshot(independent)
        wait_for(app, lambda: str(independent) in saved)
        assert target.read_bytes() == b'previous file'
        release_first.set()
        wait_for(app, lambda: saved.count(str(target)) == 1)
        window.snapshot_one(second.key)
        wait_for(app, lambda: saved.count(str(target)) == 2)
        frame = cv2.imdecode(np.fromfile(str(target), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert int(frame[0, 0, 0]) == 220
    finally:
        release_first.set()


def test_failed_photo_can_retry_the_same_path(app, window, tmp_path, monkeypatch):
    worker = window.sessions['demo:0'].worker
    target = tmp_path / 'retry.png'
    target.write_bytes(b'previous file')
    encode = cv2.imencode
    failed, saved = [], []
    worker.error.connect(failed.append)
    worker.snapshot_saved.connect(saved.append)
    monkeypatch.setattr(backend.cv2, 'imencode', lambda *args: (False, None))
    assert worker.take_snapshot(target, overwrite=True)
    wait_for(app, lambda: bool(failed))
    assert target.read_bytes() == b'previous file'
    monkeypatch.setattr(backend.cv2, 'imencode', encode)
    assert worker.take_snapshot(target, overwrite=True)
    wait_for(app, lambda: saved == [str(target)])


def test_closing_during_startup_does_not_start_demo_workers(app, tmp_path):
    release_scan = Event()

    class SlowScan(QThread):
        def run(self):
            release_scan.wait(3)

    class SlowScanWindow(MainWindow):
        def refresh_devices(self):
            self.scan = SlowScan(self)
            self.scan.start()

    w = SlowScanWindow(tmp_path, demo=True, demo_count=1)
    w.show()
    try:
        wait_for(app, lambda: w.scan is not None and w.scan.isRunning())
        assert not w.sessions
        w.close()
        # Leave the window in its closing state past the scheduled demo startup.
        QTimer.singleShot(260, release_scan.set)
        wait_for(app, lambda: not w.isVisible(), timeout=1.5)
        assert not w.sessions
    finally:
        release_scan.set()
        for session in w.sessions.values():
            session.worker.shutdown()
        wait_for(app, lambda: not w.isVisible())


def test_native_only_stops_capture_and_releases_queued_photo_when_driver_size_changes(app, tmp_path, monkeypatch):
    next_frame, released = Event(), Event()

    class ChangingCamera:
        def __init__(self, *args):
            self.count = 0

        def isOpened(self):
            return True

        def set(self, *args):
            return True

        def get(self, *args):
            return 20

        def release(self):
            released.set()

        def read(self):
            self.count += 1
            if self.count == 1:
                return True, np.zeros((240, 320, 3), np.uint8)
            next_frame.wait(3)
            return True, np.zeros((120, 160, 3), np.uint8)

    monkeypatch.setattr(backend.cv2, 'VideoCapture', ChangingCamera)
    monkeypatch.setattr(backend, 'read_camera_zoom', lambda *_: {})
    monkeypatch.setattr(backend, 'enumerate_camera_modes', lambda *_: [])
    worker = CameraWorker(0, CameraConfig(320, 240, 20, native_only=True))
    frames, errors = [], []
    worker.frame_ready.connect(frames.append)
    worker.error.connect(errors.append)
    worker.start()
    try:
        wait_for(app, lambda: bool(frames))
        target = tmp_path / 'strict.png'
        assert worker.take_snapshot(target)
        next_frame.set()
        wait_for(app, lambda: not worker.isRunning(), timeout=1.5)
        assert any('160 × 120' in message and '原生分辨率' in message for message in errors)
        assert released.is_set() and not target.exists() and not worker.snapshot_target_busy(target)
    finally:
        next_frame.set()
        worker.shutdown()
        assert worker.wait(3000)


def test_model_send_waits_for_new_preview_after_format_acknowledgement(app, window, monkeypatch):
    entered, release = Event(), Event()
    original = backend._demo_frame

    def delayed_frame(width, height, *args):
        if width == 640:
            entered.set()
            release.wait(3)
        return original(width, height, *args)

    monkeypatch.setattr(backend, '_demo_frame', delayed_frame)
    session = window.sessions['demo:0']
    window.show_model_dialog()
    dialog = window.model_dialog
    dialog.enabled.click()
    assert dialog.send.isEnabled() and session.tile.view.image.width() == 320
    sent = []
    monkeypatch.setattr(window.model_client, 'analyze', lambda *args: sent.append(args[-1].width()))
    try:
        window.resolution.setEditText('640 x 360')
        window.apply_format()
        wait_for(app, lambda: entered.is_set() and not session.format_pending)
        assert session.config.width == 640
        assert window._model_frame() == ('', None) and not dialog.send.isEnabled()
        dialog.send.click()
        assert not sent
        release.set()
        wait_for(app, dialog.send.isEnabled)
        dialog.send.click()
        assert sent == [640]
    finally:
        release.set()
