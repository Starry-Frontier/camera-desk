"""Timed capture, cancellation, real files, device feedback and clickable controls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time

import cv2
import numpy as np
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import multicam.capture as backend
from multicam.capture import CameraConfig, CameraWorker
from multicam.devices import CameraDevice
from multicam.ui import MainWindow
from multicam.timer_dialog import TimerDialog
from multicam.storage import estimate_storage
from test_capture import until, stop_all
from test_saving import close_window


def simulated(tmp_path, count=2):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.prefs['cameras'] = {'demo:%d' % i: {'config': {'width': 320, 'height': 240, 'fps': 20}}
                               for i in range(count)}
    window.show()
    window.add_demo_cameras(count)
    until(app, lambda: len(window.sessions) == count and all(s.ready for s in window.sessions.values()))
    return app, window


def video_seconds(path):
    cap = cv2.VideoCapture(str(path))
    try:
        assert cap.isOpened()
        frames = 0
        fps = cap.get(cv2.CAP_PROP_FPS)
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            assert frame.shape[:2] == (240, 320)
            frames += 1
        assert frames > 0
        return frames / fps
    finally:
        cap.release()


def test_worker_deadline_stops_when_gui_is_not_processing_events(tmp_path):
    app = QApplication.instance() or QApplication([])
    worker = CameraWorker('demo:0', CameraConfig(320, 240, 20))
    events = []
    worker.recording_changed.connect(lambda active, path: events.append((active, path)))
    try:
        worker.start()
        worker.start_recording(tmp_path / 'automatic.avi', duration_seconds=.6)
        until(app, lambda: events and events[0][0])
        # No GUI timer can fire during this interval; the worker must stop itself.
        time.sleep(1.0)
        until(app, lambda: len(events) == 2)
        assert events[-1] == (False, str(tmp_path / 'automatic.avi'))
        assert abs(video_seconds(tmp_path / 'automatic.avi') - .6) < .12
        assert worker.isRunning(), 'automatic stop must retain the preview'
    finally:
        stop_all(app, [worker])


def test_eight_scheduled_recordings_allow_interval_photos_without_restart(tmp_path):
    app, window = simulated(tmp_path, 8)
    events = {key: [] for key in window.sessions}
    for key, s in window.sessions.items():
        s.worker.recording_changed.connect(lambda active, path, key=key: events[key].append(active))
    try:
        window.create_capture_plan(list(window.sessions), 'video', time.time() + .4, 1.6)
        assert not any(s.recording for s in window.sessions.values())
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        window.create_capture_plan(list(window.sessions), 'photo', time.time(), .85, .25)
        window.remember_media_format('photo', '.jpg')
        until(app, lambda: not any(s.recording or s.pending_record or s.photo_plan for s in window.sessions.values()))
        until(app, lambda: len(list(window.output_dir.glob('*.png'))) >= 24)
        assert all(states == [True, False] for states in events.values())
        assert not list(window.output_dir.glob('*.jpg')), 'task format is frozen at creation'
        paths = list(window.output_dir.glob('*.avi'))
        assert len(paths) == 8
        assert all(abs(video_seconds(path) - 1.6) < .15 for path in paths)
        for photo in window.output_dir.glob('*.png'):
            assert cv2.imdecode(np.fromfile(str(photo), np.uint8), 1).shape[:2] == (240, 320)
    finally:
        close_window(app, window)


def test_separate_duration_manual_stop_and_cancel_future_tasks(tmp_path):
    app, window = simulated(tmp_path)
    a, b = list(window.sessions.values())
    try:
        window.create_capture_plan([a.key], 'video', time.time(), .6)
        window.create_capture_plan([b.key], 'video', time.time(), 3)
        until(app, lambda: a.recording and b.recording)
        until(app, lambda: not a.recording)
        assert b.recording
        b.tile.photo_btn.click()
        until(app, lambda: len(list(window.output_dir.glob('*.png'))) == 1)
        window.toggle_record(b.key, prompt=False)
        until(app, lambda: not b.recording and not b.pending_record)
        window.create_capture_plan([a.key, b.key], 'video', time.time() + 120, 1)
        a.tile.rec_btn.click()
        assert a.schedule is None and b.schedule
        window.cancel_capture_plans()
        assert b.schedule is None
        window.create_capture_plan([a.key], 'video', time.time() - 61, 1)
        assert a.schedule is None and not a.pending_record
        assert len(list(window.output_dir.glob('*.avi'))) == 2
        with pytest.raises(ValueError):
            window.create_capture_plan([a.key], 'photo', time.time(), 0, .2)
    finally:
        close_window(app, window)


def test_close_cancels_future_photo_and_recording_plans(tmp_path):
    app, window = simulated(tmp_path)
    window.create_capture_plan(['demo:0'], 'video', time.time() + 1, 2)
    window.create_capture_plan(['demo:1'], 'photo', time.time() + 1, 2, .2)
    close_window(app, window)
    assert not window.capture_timer.isActive()
    assert not any(s.worker.isRunning() for s in window.sessions.values())
    assert not list(window.output_dir.glob('*'))


def test_future_photos_do_not_take_over_record_stop_and_cancel_is_per_camera(tmp_path):
    app, window = simulated(tmp_path)
    a, b = list(window.sessions.values())
    try:
        window.toggle_record(a.key, prompt=False)
        until(app, lambda: a.recording)
        window.create_capture_plan([a.key, b.key], 'photo', time.time() + 120, 10, 1)
        a.tile.rec_btn.click()
        until(app, lambda: not a.recording and not a.pending_record)
        assert a.schedule and b.schedule
        a.tile.timer_btn.click()
        assert a.schedule is None and b.schedule
        window.cancel_capture_plans()
    finally:
        close_window(app, window)


def test_duration_preferences_zero_rejected_and_user_changes_do_not_extend_recording(tmp_path):
    app, window = simulated(tmp_path)
    try:
        window.duration_enabled.setChecked(True)
        window.record_duration.setValue(0)
        window.toggle_record('demo:0', prompt=False)
        assert not window.sessions['demo:0'].pending_record
        window.record_duration.setValue(1)
        window.toggle_record('demo:0', prompt=False)
        until(app, lambda: window.sessions['demo:0'].recording)
        window.record_duration.setValue(10)
        until(app, lambda: not window.sessions['demo:0'].recording)
        assert abs(video_seconds(next(window.output_dir.glob('*.avi'))) - 1) < .15
    finally:
        close_window(app, window)
    restored = MainWindow(tmp_path)
    assert restored.record_duration.value() == 10 and restored.duration_enabled.isChecked()
    restored.show()
    close_window(app, restored)


class ZoomCapture:
    def __init__(self, index):
        self.index, self.zoom, self.allow = index, 100, True
    def get(self, prop):
        return self.zoom if prop == cv2.CAP_PROP_ZOOM else 20
    def set(self, prop, value):
        if prop == cv2.CAP_PROP_ZOOM:
            if not self.allow:
                return False
            self.zoom = int(value)
        return True
    def read(self):
        time.sleep(.025)
        return True, np.zeros((240, 320, 3), np.uint8)
    def release(self):
        pass


def test_zoom_capability_per_camera_limits_and_rejected_driver_feedback(tmp_path, monkeypatch):
    caps = {'supported': True, 'minimum': 100, 'maximum': 400, 'step': 10, 'default': 100, 'current': 100}
    monkeypatch.setattr(backend, 'read_camera_zoom', lambda index: dict(caps) if index != 1 else {'supported': False})
    monkeypatch.setattr(backend, 'enumerate_camera_modes', lambda index: [])
    handles = {}
    def open_format(self, config, require_exact):
        capture = handles.setdefault(self.device_id, ZoomCapture(self.device_id))
        return capture, np.zeros((240, 320, 3), np.uint8)
    monkeypatch.setattr(CameraWorker, '_open_format', open_format)
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    try:
        for index in range(3):
            window.add_camera(CameraDevice(index, '测试镜头%d' % index, 'fake:%d' % index))
        until(app, lambda: all(s.ready for s in window.sessions.values()))
        a, unavailable, b = list(window.sessions.values())
        assert a.tile.zoom_apply.isEnabled() and b.tile.zoom_apply.isEnabled()
        assert not unavailable.tile.zoom_apply.isEnabled()
        a.tile.zoom_value.setValue(120)
        a.tile.zoom_apply.click()
        b.tile.zoom_value.setValue(300)
        b.tile.zoom_apply.click()
        until(app, lambda: handles[0].zoom == 120 and handles[2].zoom == 300)
        assert handles[1].zoom == 100
        handles[0].allow = False
        a.tile.zoom_value.setValue(150)
        a.tile.zoom_apply.click()
        until(app, lambda: bool(a.last_error))
        assert handles[0].zoom == 120 and a.worker.isRunning()
        a.worker.set_hardware('zoom', 125)
        until(app, lambda: '步长' in a.last_error)
        assert handles[2].zoom == 300
    finally:
        close_window(app, window)


def test_selection_button_and_storage_estimate_update(tmp_path):
    app, window = simulated(tmp_path)
    dialog = None
    try:
        window.select_session('demo:0')
        QTest.mouseClick(window.resolution, Qt.LeftButton, pos=QPoint(window.resolution.width() - 14, window.resolution.height() // 2))
        until(app, lambda: window.resolution.view().isVisible())
        window.resolution.hidePopup()
        dialog = TimerDialog(list(window.sessions.values()), None, window.output_dir, window.media_formats, window)
        dialog.show()
        initial = dialog.estimate.text()
        dialog.kind.setCurrentIndex(1)
        dialog.interval.setValue(2)
        dialog.duration.setValue(10)
        app.processEvents()
        assert dialog.interval.isEnabled() and '10 张照片' in dialog.estimate.text()
        assert initial != dialog.estimate.text() and '磁盘剩余' in dialog.estimate.text()
        bmp = estimate_storage([CameraConfig(320, 240, 20)] * 2, 10, '.bmp', 2)
        assert bmp['photos'] == 10 and bmp['minimum_bytes'] >= 10 * 320 * 240 * 3
        dialog.accept()
        assert dialog.plan['kind'] == 'photo' and dialog.plan['duration'] == 10
    finally:
        if dialog:
            dialog.close()
        close_window(app, window)
