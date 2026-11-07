"""Regress the capture, timer and settings defects found on 2026-11-03.

No physical cameras are used; saved media exercises the real encoders.
"""
import json
import os
from pathlib import Path
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

import multicam.capture as backend
from multicam.capture import CameraConfig, CameraWorker, ImageSettings, SaveRequest
from multicam.devices import CameraDevice
from multicam.formats import CameraMode
from multicam.ui import MainWindow


class AuditWindow(MainWindow):
    def refresh_devices(self):
        # Keep this review independent of real camera discovery and permissions.
        pass


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert predicate(), 'asynchronous operation timed out'


@pytest.fixture
def window(app, tmp_path):
    result = AuditWindow(tmp_path / 'data')
    result.prefs['cameras'] = {'demo:0': {'config': {'width': 320, 'height': 240, 'fps': 20}}}
    result.show()
    result.add_demo_cameras(1)
    wait_for(app, lambda: result.sessions['demo:0'].ready)
    try:
        yield result
    finally:
        result.close()
        wait_for(app, lambda: not result.isVisible())
        assert all(s.worker.wait(1000) for s in result.sessions.values())


def test_interval_photos_keep_creation_directory(app, window, tmp_path):
    original = tmp_path / 'original'
    changed = tmp_path / 'changed'
    original.mkdir()
    changed.mkdir()
    window.output_dir = original
    s = window.sessions['demo:0']
    saved = []
    s.worker.snapshot_saved.connect(saved.append)
    window.create_capture_plan([s.key], 'photo', time.time(), 2, .25)
    wait_for(app, lambda: len(saved) == 1)
    window.output_dir = changed
    wait_for(app, lambda: len(saved) >= 3)
    parents = [str(Path(path).parent) for path in saved]
    assert all(Path(path).parent == original for path in saved), parents


def test_interval_task_survives_temporary_format_change(app, window, monkeypatch):
    s = window.sessions['demo:0']
    saved = []
    s.worker.snapshot_saved.connect(saved.append)
    window.create_capture_plan([s.key], 'photo', time.time(), 3, .25)
    wait_for(app, lambda: len(saved) == 1)
    end = s.photo_plan['end']
    apply_format = s.worker._apply_format

    def slow_format(*args, **kwargs):
        # A real DirectShow graph rebuild can readily span multiple 100 ms ticks.
        time.sleep(.3)
        return apply_format(*args, **kwargs)

    monkeypatch.setattr(s.worker, '_apply_format', slow_format)
    window.select_session(s.key)
    window.resolution.setEditText('640 x 360')
    window.apply_format()
    wait_for(app, lambda: not s.format_pending)
    assert s.config.width == 640
    assert s.photo_plan is not None, 'changing format silently cancelled the live photo task'
    assert s.photo_plan['end'] == end
    wait_for(app, lambda: any(cv2.imdecode(np.fromfile(path, np.uint8), 1).shape[:2] == (360, 640)
                             for path in saved))


@pytest.mark.parametrize('kind', ['photo', 'video'])
def test_schedule_waits_for_format_change_instead_of_cancelling(app, window, monkeypatch, kind):
    s = window.sessions['demo:0']
    window.create_capture_plan([s.key], kind, time.time() + .15, 1, .2)
    starts_at = s.schedule['starts_at']
    apply_format = s.worker._apply_format

    def slow_format(*args, **kwargs):
        time.sleep(.4)
        return apply_format(*args, **kwargs)

    monkeypatch.setattr(s.worker, '_apply_format', slow_format)
    window.select_session(s.key)
    window.resolution.setEditText('640 x 360')
    window.apply_format()
    wait_for(app, lambda: time.time() >= starts_at + .05)
    assert s.format_pending and s.schedule is not None
    wait_for(app, lambda: not s.format_pending)
    if kind == 'photo':
        wait_for(app, lambda: s.photo_plan is not None)
        wait_for(app, lambda: bool(list(window.output_dir.glob('*.png'))))
    else:
        wait_for(app, lambda: s.recording)
    assert s.schedule is None


def test_photo_task_still_expires_during_format_change(app, window, monkeypatch):
    s = window.sessions['demo:0']
    window.create_capture_plan([s.key], 'photo', time.time(), .2, .1)
    apply_format = s.worker._apply_format

    def slow_format(*args, **kwargs):
        time.sleep(.4)
        return apply_format(*args, **kwargs)

    monkeypatch.setattr(s.worker, '_apply_format', slow_format)
    window.select_session(s.key)
    window.resolution.setEditText('640 x 360')
    window.apply_format()
    wait_for(app, lambda: s.photo_plan is None)
    assert s.format_pending, 'task end must not be extended by a pending format'


@pytest.mark.parametrize('native_only', [False, True])
def test_default_negotiation_tries_native_codec_before_scaling(app, monkeypatch, native_only):
    attempts = []
    handles = []

    class Capture:
        def __init__(self, *args):
            assert all(handle.released for handle in handles), 'device was opened twice'
            self.codec = ''
            self.released = False
            handles.append(self)

        def isOpened(self):
            return True

        def set(self, prop, value):
            if prop == cv2.CAP_PROP_FOURCC:
                self.codec = int(value).to_bytes(4, 'little').decode('ascii')
            return True

        def read(self):
            attempts.append(self.codec)
            size = (480, 640) if self.codec == 'YUY2' else (240, 320)
            return True, np.zeros((*size, 3), np.uint8)

        def release(self):
            self.released = True

    monkeypatch.setattr(backend.cv2, 'VideoCapture', Capture)
    worker = CameraWorker(0, CameraConfig(640, 480, 20))
    worker._modes = [CameraMode(640, 480, 20, codec) for codec in ('MJPG', 'YUY2')]
    capture, frame = worker._open_format(worker._config, require_exact=native_only)
    capture.release()
    assert frame.shape[:2] == (480, 640), 'native YUY2 was skipped; attempted %r' % attempts
    assert attempts == ['MJPG', 'YUY2']
    assert all(handle.released for handle in handles)


@pytest.mark.parametrize('native_only', [False, True])
def test_format_fallback_and_strict_failure_release_other_handles(app, monkeypatch, native_only):
    handles = []

    class Capture:
        def __init__(self, *args):
            assert all(handle.released for handle in handles)
            self.released = False
            handles.append(self)

        def isOpened(self):
            return True

        def set(self, *args):
            return True

        def read(self):
            return True, np.zeros((240, 320, 3), np.uint8)

        def release(self):
            self.released = True

    monkeypatch.setattr(backend.cv2, 'VideoCapture', Capture)
    worker = CameraWorker(0, CameraConfig(640, 480, 20))
    if native_only:
        with pytest.raises(RuntimeError, match='设备实际返回'):
            worker._open_format(worker._config, require_exact=True)
    else:
        capture, frame = worker._open_format(worker._config, require_exact=False)
        assert frame.shape[:2] == (240, 320)
        assert len(handles) == 4, 'search both codecs and driver default before reopening fallback'
        capture.release()
    assert all(handle.released for handle in handles)


def test_small_arrival_jitter_does_not_discard_half_the_samples(app):
    class Writer:
        def __init__(self):
            self.samples = []

        def write(self, frame):
            self.samples.append(int(frame[0, 0, 0]))

    worker = CameraWorker('demo:0', CameraConfig(320, 240, 10))
    writer = Writer()
    worker._writer = writer
    worker._record_size = (2, 2)
    worker._record_fps = 10
    worker._record_started = 0
    worker._record_last_frame = np.zeros((2, 2, 3), np.uint8)
    # Six frames at an average 10 fps, with only +/- 0.1 ms timing variation.
    for sample, arrived in enumerate((0, .1001, .1999, .3001, .3999, .5001)):
        worker._record_frame(np.full((2, 2, 3), sample, np.uint8), arrived)
    worker._writer = None
    assert writer.samples == list(range(5)), 'six source samples became %r' % writer.samples


@pytest.mark.parametrize('duration', [.02, .6])
def test_real_video_retains_jittered_samples_and_final_buffered_frame(app, tmp_path, monkeypatch, duration):
    path = tmp_path / 'jitter.avi'
    worker = CameraWorker('demo:0', CameraConfig(320, 240, 10))
    errors, records = [], []
    worker.error.connect(errors.append)
    worker.recording_changed.connect(lambda active, path: records.append((active, path)))
    worker._frame = np.full((240, 320, 3), 20, np.uint8)
    worker._begin_recording(SaveRequest(str(path)))
    started = worker._record_started
    try:
        for sample, arrived in enumerate((0, .1001, .1999, .3001, .3999, .5001)):
            if arrived < duration:
                worker._record_frame(np.full((240, 320, 3), 20 + sample * 30, np.uint8), started + arrived)
        with monkeypatch.context() as clock:
            clock.setattr(backend.time, 'perf_counter', lambda: started + duration)
            worker._finish_recording()
        assert not errors
        assert records == [(True, str(path)), (False, str(path))]
        cap = cv2.VideoCapture(str(path))
        try:
            assert cap.isOpened()
            means = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                means.append(float(frame.mean()))
            expected = [20] if duration < .1 else [20 + sample * 30 for sample in range(6)]
            assert means == pytest.approx(expected, abs=3)
            assert len(means) / cap.get(cv2.CAP_PROP_FPS) == pytest.approx(max(.1, duration))
        finally:
            cap.release()
    finally:
        if worker._writer is not None:
            worker._finish_recording()


def test_recording_still_rejects_excessive_catchup(app, monkeypatch):
    worker = CameraWorker('demo:0', CameraConfig(320, 240, 10))
    worker._record_size = (2, 2)
    worker._record_fps = 10
    worker._record_started = 0
    worker._record_last_frame = np.zeros((2, 2, 3), np.uint8)
    errors, stopped = [], []
    worker.error.connect(errors.append)
    monkeypatch.setattr(worker, '_finish_recording', lambda pad=True: stopped.append(pad))
    worker._record_frame(worker._record_last_frame, 13)
    assert stopped == [False]
    assert len(errors) == 1 and '落后' in errors[0]


@pytest.mark.parametrize('camera_settings', [
    {'config': {'width': 0, 'height': 240, 'fps': 20}},
    {'config': {'fps': 'invalid'}},
    {'config': {'width': float('inf')}},
    {'config': []},
    {'image': {'brightness': 'invalid'}},
    {'image': None},
    [],
])
def test_invalid_saved_camera_config_falls_back_to_defaults(app, tmp_path, camera_settings):
    data = tmp_path / 'invalid-settings'
    data.mkdir()
    (data / 'settings.json').write_text(json.dumps({
        'cameras': {'demo:0': camera_settings}
    }), encoding='utf-8')
    window = AuditWindow(data)
    window.show()
    try:
        window.add_camera(CameraDevice(0, 'Audit demo', 'demo:0'), demo=True)
        wait_for(app, lambda: window.sessions['demo:0'].ready)
        assert window.sessions['demo:0'].config == CameraConfig()
        assert window.sessions['demo:0'].image == ImageSettings()
    finally:
        window.close()
        wait_for(app, lambda: not window.isVisible())
    restored = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
    assert restored['cameras']['demo:0']['config']['width'] == 1280


@pytest.mark.parametrize('cameras', [[], None, 'invalid'])
def test_invalid_camera_settings_collection_can_be_saved_again(app, tmp_path, cameras):
    data = tmp_path / 'invalid-collection'
    data.mkdir()
    (data / 'settings.json').write_text(json.dumps({'cameras': cameras}), encoding='utf-8')
    window = AuditWindow(data)
    window.show()
    try:
        window.add_demo_cameras(1)
        wait_for(app, lambda: window.sessions['demo:0'].ready)
    finally:
        window.close()
        wait_for(app, lambda: not window.isVisible())
    restored = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
    assert isinstance(restored['cameras'], dict) and 'demo:0' in restored['cameras']
