"""Mix global start/stop with independent, staggered camera recordings."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import threading
import time

import cv2
from PySide6.QtWidgets import QDialog, QFileDialog

from multicam.devices import CameraDevice
from multicam.save_dialog import BatchSaveDialog
from test_capture import until
from test_saving import close_window, demo_window


def readable_videos(folder):
    videos = list(folder.glob('*.avi'))
    for path in videos:
        capture = cv2.VideoCapture(str(path))
        try:
            ok, frame = capture.read()
            assert ok and frame is not None, str(path)
            assert capture.get(cv2.CAP_PROP_FRAME_COUNT) > 0
        finally:
            capture.release()
    return videos


def test_camera_buttons_start_and_stop_independently(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    folder = tmp_path / '各机位分别开始'
    folder.mkdir()
    chosen = iter([folder / '前视 先开始.avi', folder / '侧视 后开始.avi'])
    dialogs = []
    events = {key: [] for key in window.sessions}
    for key, s in window.sessions.items():
        s.worker.recording_changed.connect(lambda active, path, key=key: events[key].append(active))
    def save(*args, **kwargs):
        dialogs.append(args)
        return str(next(chosen)), 'AVI 视频 (*.avi)'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    a, b = window.sessions['demo:0'], window.sessions['demo:1']
    try:
        a.tile.rec_btn.click()
        until(app, lambda: a.recording)
        assert not b.recording and b.tile.rec_btn.isEnabled()
        assert window.all_record.isEnabled() and '其余' in window.all_record.text()
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .25)
        b.tile.rec_btn.click()
        until(app, lambda: b.recording)
        assert a.recording and events['demo:0'] == [True]
        b_path = b.pending_save_path
        a.tile.rec_btn.click()
        until(app, lambda: not a.recording and not a.pending_record)
        assert b.recording and events['demo:1'] == [True] and b.pending_save_path == b_path
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .25)
        b.tile.rec_btn.click()
        until(app, lambda: not b.recording and not b.pending_record)
        assert events == {'demo:0': [True, False], 'demo:1': [True, False]}
        assert len(dialogs) == 2, 'Only starts ask for file names'
        assert len(readable_videos(folder)) == 2
        assert not window.all_stop.isEnabled() and window.all_record.isEnabled()
    finally:
        close_window(app, window)


def test_global_start_adds_remaining_cameras_and_keeps_existing_recordings(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    window.add_camera(CameraDevice(2, '演示机位 C', 'demo:2'), demo=True)
    folder = tmp_path / '全部与单路混用'
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(folder / '持续的前视录像.avi'), 'AVI 视频 (*.avi)'))
    plans = []
    accepted = []
    def choose_batch(self):
        keys = [s.key for s in self.sessions]
        plans.append(keys)
        assert self.directory.text() == str(folder)
        if len(plans) == 1:
            return QDialog.Rejected  # Cancelling must not stop an active camera.
        accepted.append(keys)
        self.prefix.setText('共同开始' if len(accepted) == 1 else '重新加入')
        self.accept()
        return self.result()
    monkeypatch.setattr(BatchSaveDialog, 'exec', choose_batch)
    events = {key: [] for key in window.sessions}
    for key, s in window.sessions.items():
        s.worker.recording_changed.connect(lambda active, path, key=key: events[key].append(active))
    try:
        until(app, lambda: all(s.ready for s in window.sessions.values()))
        a, b, c = [window.sessions['demo:%d' % n] for n in range(3)]
        a.tile.rec_btn.click()
        until(app, lambda: a.recording)
        original_path = a.pending_save_path
        window.all_record.click()
        assert a.recording and not b.recording and not c.recording
        window.all_record.click()
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        assert accepted == [['demo:1', 'demo:2']]
        assert a.pending_save_path == original_path and events['demo:0'] == [True]
        assert not window.all_record.isEnabled()
        window.record_all()  # An extra start must neither restart nor stop anything.
        assert len(plans) == 2 and all(s.recording for s in window.sessions.values())
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .25)
        b.tile.rec_btn.click()
        until(app, lambda: not b.recording and not b.pending_record)
        assert a.recording and c.recording
        window.all_record.click()
        until(app, lambda: b.recording)
        assert accepted[-1] == ['demo:1']
        assert events['demo:0'] == [True] and events['demo:2'] == [True]
        assert a.pending_save_path == original_path
        window.all_stop.click()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        assert events == {'demo:0': [True, False], 'demo:1': [True, False, True, False], 'demo:2': [True, False]}
        videos = readable_videos(folder)
        assert {path.name for path in videos} == {'持续的前视录像.avi', '共同开始_机位02.avi',
                                                  '共同开始_机位03.avi', '重新加入_机位02.avi'}
    finally:
        close_window(app, window)


def test_stop_all_cancels_pending_start_and_one_failure_does_not_block_others(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    window.add_camera(CameraDevice(2, '不能写 AVI 的演示机位', 'demo:2'), demo=True)
    gate, entered = threading.Event(), threading.Event()
    a, b, bad = [window.sessions['demo:%d' % n] for n in range(3)]
    original_begin = a.worker._begin_recording
    def slow_begin(request):
        entered.set()
        gate.wait(4)
        original_begin(request)
    monkeypatch.setattr(a.worker, '_begin_recording', slow_begin)
    trace = []
    a.worker.recording_changed.connect(lambda active, path: trace.append((active, a.pending_record, a.record_action)))
    try:
        until(app, lambda: all(s.ready for s in window.sessions.values()))
        bad.worker.set_format(321, 241, 15)  # AVI/MJPG rejects odd dimensions.
        until(app, lambda: bad.config.width == 321)
        window.toggle_record('demo:0', prompt=False)
        until(app, entered.is_set)
        assert a.pending_record and not a.recording and a.record_action == 'start'
        assert a.tile.rec_btn.isEnabled() and window.all_record.isEnabled()
        window.record_all(prompt=False)
        until(app, lambda: b.recording and bool(bad.last_error))
        assert not bad.recording and not bad.pending_record
        assert a.pending_record and b.recording
        window.all_stop.click()
        assert a.record_action == 'stop' and a.pending_record
        gate.set()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        assert trace[0] == (True, True, 'stop'), 'Late start acknowledgements must preserve a queued stop'
        assert trace[-1] == (False, False, None)
        assert len(readable_videos(window.output_dir)) == 2
        assert window.all_record.isEnabled() and not window.all_stop.isEnabled()
    finally:
        gate.set()
        close_window(app, window)
