"""Reproduce capture, shutdown and preview races without physical cameras."""
from ctypes import c_void_p
from pathlib import Path
from threading import Event
import time

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox

import multicam.capture as backend
import multicam.devices as directshow
from multicam.capture import CameraConfig, CameraWorker, SaveRequest
from multicam.devices import CameraDevice
from multicam.formats import CameraMode
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


@pytest.mark.parametrize('failure', ['unreadable', 'missing_frames', 'damaged_middle'])
def test_damaged_recording_is_never_committed(app, tmp_path, monkeypatch, failure):
    overwrite = failure != 'unreadable'
    path = tmp_path / 'recording.avi'
    original_factory = cv2.VideoWriter
    frame = np.zeros((120, 160, 3), np.uint8)
    if overwrite:
        original = original_factory(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 20, (160, 120))
        for _ in range(5):
            original.write(frame)
        original.release()
        previous = path.read_bytes()

    class BrokenWriter:
        def __init__(self, filename, *args):
            self.path = Path(filename)
            self.writer = original_factory(filename, *args) if overwrite else None
            self.wrote_frame = False

        def isOpened(self):
            return True

        def write(self, image):
            if self.writer is not None and (failure == 'damaged_middle' or not self.wrote_frame):
                self.writer.write(image)
                self.wrote_frame = True

        def release(self):
            if self.writer is None:
                self.path.write_bytes(b'undecodable data')
            else:
                self.writer.release()
            if failure == 'damaged_middle':
                data = bytearray(self.path.read_bytes())
                # Damage a single MJPEG payload while preserving the AVI index.
                chunk = data.index(b'movi') + 4
                for index in range(4):
                    assert data[chunk:chunk+4] == b'00dc'
                    size = int.from_bytes(data[chunk+4:chunk+8], 'little')
                    start = chunk + 8
                    chunk = start + size + size % 2
                data[start:start+size] = bytes(size)
                self.path.write_bytes(data)

    monkeypatch.setattr(cv2, 'VideoWriter', BrokenWriter)
    worker = CameraWorker('demo:0', CameraConfig(160, 120, 20))
    worker._frame = frame
    errors, events = [], []
    worker.error.connect(errors.append)
    worker.recording_changed.connect(lambda active, result: events.append((active, result)))
    worker._begin_recording(SaveRequest(str(path), overwrite=overwrite))
    worker._write_recording_until(6)
    worker._record_deadline = worker._record_started + 6 / worker._record_fps
    worker._finish_recording()
    assert events == [(True, str(path)), (False, '')]
    assert errors and '录像保存失败' in errors[-1]
    if failure == 'damaged_middle':
        assert '第 4 / 6 帧' in errors[-1]
    assert list(tmp_path.glob('.recording-*.avi'))
    if overwrite:
        assert path.read_bytes() == previous
    else:
        assert not path.exists()


@pytest.mark.parametrize('reorder_during_probe', [False, True])
def test_device_and_capabilities_match_when_scan_order_changes(app, window, monkeypatch, reorder_during_probe):
    first, second = CameraDevice(0, 'Camera A', 'camera-a'), CameraDevice(1, 'Camera B', 'camera-b')
    reordered = [CameraDevice(0, second.name, second.device_id), CameraDevice(1, first.name, first.device_id)]
    current = [first, second] if reorder_during_probe else list(reordered)
    monkeypatch.setattr(backend, 'enumerate_cameras', lambda: current)

    def lookup(device):
        return current[device].device_id if isinstance(device, int) else device

    def read_zoom(device):
        current[:] = reordered
        if lookup(device) == first.device_id:
            return {'supported': True, 'minimum': 100, 'maximum': 200, 'step': 10, 'default': 100}
        return {'supported': False}

    def read_modes(device):
        mode = CameraMode(320, 240, 20, 'MJPG') if lookup(device) == first.device_id else CameraMode(800, 600, 20, 'MJPG')
        return [mode]

    monkeypatch.setattr(backend, 'read_camera_zoom', read_zoom)
    monkeypatch.setattr(backend, 'enumerate_camera_modes', read_modes)
    opened = []

    class Capture:
        def __init__(self, index, api):
            opened.append(current[index].device_id)

        def isOpened(self):
            return True

        def set(self, *_):
            return True

        def get(self, _):
            return 20.0

        def read(self):
            return True, np.zeros((240, 320, 3), np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(cv2, 'VideoCapture', Capture)
    window.prefs['cameras'][first.device_id] = {'config': {'width': 320, 'height': 240, 'fps': 20}}
    window.devices_found([first, second], '')
    window.add_selected_device()
    session = window.sessions[first.device_id]
    wait_for(app, lambda: not session.tile.view.image.isNull())
    assert opened == [first.device_id]
    assert session.zoom_caps['maximum'] == 200
    assert [mode['width'] for mode in session.modes] == [320]


def test_directshow_binds_the_matched_moniker_instead_of_the_old_index(monkeypatch):
    monikers = iter((101, 102))  # B is first after the device order changes.
    released, bound = [], []

    def next_moniker(_enumerator, _count, moniker, fetched):
        moniker._obj.value = next(monikers)
        fetched._obj.value = 1
        return 0

    def bind(moniker, _context, _left, _iid, camera_filter):
        bound.append(moniker.value)
        camera_filter._obj.value = 303
        return 0

    monkeypatch.setattr(directshow, '_variant_library', lambda: None)
    monkeypatch.setattr(directshow, '_camera_bind_context', lambda ole: c_void_p(200))
    monkeypatch.setattr(directshow, '_open_property_bag', lambda moniker: c_void_p(moniker.value + 10))
    monkeypatch.setattr(directshow, '_device_identity', lambda moniker, *_: {101: 'b', 102: 'a'}[moniker.value])
    monkeypatch.setattr(directshow, '_method', lambda ptr, slot, *_: {3: next_moniker, 8: bind}[slot])
    monkeypatch.setattr(directshow, '_release', lambda pointer: released.append(pointer.value))
    assert directshow._bind_camera_filter(None, c_void_p(300), 'a').value == 303
    assert bound == [102]
    assert released == [111, 101, 112, 102, 200]


def test_high_fractional_capture_rate_can_record_mp4(app, tmp_path):
    path = tmp_path / 'high-rate.mp4'
    fps = 119.999
    worker = CameraWorker('demo:0', CameraConfig(160, 120, fps))
    worker._frame = np.zeros((120, 160, 3), np.uint8)
    worker._begin_recording(SaveRequest(str(path)))
    assert worker._writer is not None and worker._config.fps == fps
    worker._write_recording_until(6)
    worker._finish_recording()
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    try:
        assert capture.read()[0]
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(120)
    finally:
        capture.release()


def test_failed_restore_stops_with_original_capture_error(app, monkeypatch):
    frame = np.zeros((240, 320, 3), np.uint8)

    class Capture:
        def read(self):
            return True, frame

        def get(self, _):
            return 20.0

        def release(self):
            pass

    def open_format(config, require_exact):
        if worker._capture is None and not previews:
            return Capture(), frame
        raise RuntimeError('设备已断开')

    worker = CameraWorker(0, CameraConfig(320, 240, 20))
    previews, errors = [], []
    worker.frame_ready.connect(previews.append)
    worker.error.connect(errors.append)
    monkeypatch.setattr(backend, 'read_camera_zoom', lambda index: {'supported': False})
    monkeypatch.setattr(backend, 'enumerate_camera_modes', lambda index: [])
    monkeypatch.setattr(worker, '_open_format', open_format)
    worker.start()
    try:
        wait_for(app, lambda: bool(previews))
        worker.set_format(640, 360, 25)
        wait_for(app, lambda: not worker.isRunning())
        assert len(errors) == 1 and '原画面恢复失败' in errors[0] and '设备已断开' in errors[0]
    finally:
        worker.shutdown()
        worker.wait(1000)


def test_timed_photo_path_error_allows_other_camera_to_capture(app, window, tmp_path, monkeypatch):
    window.add_demo_cameras(2)
    first, second = window.sessions.values()
    wait_for(app, lambda: first.ready and second.ready)
    window.capture_timer.stop()
    wall = time.time()
    monkeypatch.setattr(time, 'time', lambda: wall)
    blocked, valid = tmp_path / 'blocked', tmp_path / 'valid'
    for session, directory in ((first, blocked), (second, valid)):
        window.output_dir = directory
        window.create_capture_plan([session.key], 'photo', wall + 5, 30, 1)

    resolve = Path.resolve

    def resolve_except_blocked(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError('保存位置不可访问')
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', resolve_except_blocked)
    wall += 5
    now = time.monotonic()
    window.tick_capture_plans()
    wait_for(app, lambda: second.last_photo is not None)
    assert first.photo_plan['next'] > now and '保存位置不可访问' in first.last_error
    assert Path(second.last_photo).parent == valid


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
