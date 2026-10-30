"""Real codecs and photos taken during uninterrupted camera recordings."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication, QFileDialog

import multicam.capture as backend
from multicam.capture import CameraConfig, CameraWorker
from multicam.save_dialog import BatchSaveDialog
from multicam.ui import MainWindow
from test_capture import stop_all, until
from test_saving import close_window, demo_window


def read_video(path):
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        assert ok and frame is not None
        frames, fps = capture.get(cv2.CAP_PROP_FRAME_COUNT), capture.get(cv2.CAP_PROP_FPS)
        assert frames > 0 and fps > 0
        return frame.shape[:2], frames / fps
    finally:
        capture.release()


def header(path):
    with path.open('rb') as stream:
        return stream.read(16)


def test_all_image_formats_have_correct_bytes_and_full_resolution(tmp_path):
    app = QApplication.instance() or QApplication([])
    worker = CameraWorker('demo:0', CameraConfig(320, 240, 20))
    frames, saved, errors = [], [], []
    worker.frame_ready.connect(lambda image: (frames.append(1), worker.acknowledge_preview()))
    worker.snapshot_saved.connect(saved.append)
    worker.error.connect(errors.append)
    suffixes = ['.png', '.jpg', '.bmp', '.tif', '.webp', '.jpeg', '.tiff']
    try:
        worker.start()
        until(app, lambda: bool(frames))
        for suffix in suffixes:
            worker.take_snapshot(tmp_path / ('中文 图片' + suffix))
        until(app, lambda: len(saved) == len(suffixes))
        assert not errors
        for suffix in suffixes:
            path = tmp_path / ('中文 图片' + suffix)
            image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape[:2] == (240, 320)
            magic = header(path)
            if suffix == '.png':
                assert magic.startswith(b'\x89PNG')
            elif suffix in ('.jpg', '.jpeg'):
                assert magic.startswith(b'\xff\xd8')
            elif suffix == '.bmp':
                assert magic.startswith(b'BM')
            elif suffix in ('.tif', '.tiff'):
                assert magic.startswith((b'II\x2a\x00', b'MM\x00\x2a'))
            else:
                assert magic[:4] == b'RIFF' and magic[8:12] == b'WEBP'
    finally:
        stop_all(app, [worker])


def test_avi_mp4_mkv_recordings_allow_photos_without_restarting(tmp_path):
    app = QApplication.instance() or QApplication([])
    workers = [CameraWorker('demo:%d' % n, CameraConfig(320, 240, 20)) for n in range(3)]
    records, photos, errors = [[], [], []], [], []
    suffixes = ['.avi', '.mp4', '.mkv']
    for n, worker in enumerate(workers):
        worker.frame_ready.connect(lambda image, worker=worker: worker.acknowledge_preview())
        worker.recording_changed.connect(lambda active, path, n=n: records[n].append(active))
        worker.snapshot_saved.connect(photos.append)
        worker.error.connect(errors.append)
    try:
        for n, worker in enumerate(workers):
            worker.start()
            worker.start_recording(tmp_path / ('录像机位%d' % n + suffixes[n]))
        until(app, lambda: all(events == [True] for events in records))
        for n, worker in enumerate(workers):
            worker.take_snapshot(tmp_path / ('录像中拍照%d' % n + ['.bmp', '.tif', '.webp'][n]))
        until(app, lambda: len(photos) == 3)
        assert records == [[True], [True], [True]]
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .35)
        for worker in workers:
            worker.stop_recording()
        until(app, lambda: records == [[True, False], [True, False], [True, False]])
        assert not errors
        for n, suffix in enumerate(suffixes):
            path = tmp_path / ('录像机位%d' % n + suffix)
            shape, seconds = read_video(path)
            assert shape == (240, 320) and seconds >= .3
            magic = header(path)
            if suffix == '.avi':
                assert magic[:4] == b'RIFF' and magic[8:12] == b'AVI '
            elif suffix == '.mp4':
                assert magic[4:8] == b'ftyp'
            else:
                assert magic.startswith(b'\x1a\x45\xdf\xa3')
    finally:
        stop_all(app, workers)


def test_slow_photo_encoding_and_photo_failure_do_not_pause_either_video(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    workers = [CameraWorker('demo:%d' % n, CameraConfig(320, 240, 20)) for n in range(2)]
    gate, entered = threading.Event(), threading.Event()
    encode = backend.cv2.imencode
    def slow_or_failed_encode(suffix, *args):
        if suffix == '.png':
            entered.set()
            gate.wait(4)
        if suffix == '.bmp':
            raise RuntimeError('模拟照片保存失败')
        return encode(suffix, *args)
    monkeypatch.setattr(backend.cv2, 'imencode', slow_or_failed_encode)
    previews, records, saved, errors = [0, 0], [[], []], [], []
    for n, worker in enumerate(workers):
        def on_frame(image, n=n, worker=worker):
            previews[n] += 1
            worker.acknowledge_preview()
        worker.frame_ready.connect(on_frame)
        worker.recording_changed.connect(lambda active, path, n=n: records[n].append((active, time.monotonic())))
        worker.snapshot_saved.connect(saved.append)
        worker.error.connect(errors.append)
    try:
        for n, worker in enumerate(workers):
            worker.start()
            worker.start_recording(tmp_path / ('持续录像%d' % n + ('.avi' if n == 0 else '.mp4')))
        until(app, lambda: all(events and events[0][0] for events in records))
        for n, worker in enumerate(workers):
            worker.take_snapshot(tmp_path / ('慢速照片%d.png' % n))
        until(app, entered.is_set)
        before = list(previews)
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .8)
        assert not saved, 'The photo encoders are deliberately still waiting'
        assert all(previews[n] - before[n] >= 5 for n in range(2)), 'Photo work must not block capture'
        assert all(len(events) == 1 and events[0][0] for events in records)
        gate.set()
        until(app, lambda: len(saved) == 2)
        workers[0].take_snapshot(tmp_path / '失败的照片.bmp')
        until(app, lambda: any('模拟照片保存失败' in error for error in errors))
        assert all(len(events) == 1 for events in records)
        for worker in workers:
            worker.stop_recording()
        until(app, lambda: all(len(events) == 2 for events in records))
        for n, events in enumerate(records):
            path = tmp_path / ('持续录像%d' % n + ('.avi' if n == 0 else '.mp4'))
            shape, seconds = read_video(path)
            elapsed = events[1][1] - events[0][1]
            assert shape == (240, 320) and seconds >= .8 and abs(seconds - elapsed) < .3
        assert not (tmp_path / '失败的照片.bmp').exists()
    finally:
        gate.set()
        stop_all(app, workers)


def test_ui_format_selectors_and_photo_buttons_work_during_recording(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    shared = tmp_path / '录制与照片共存'
    shared.mkdir()
    records = {key: [] for key in window.sessions}
    for key, s in window.sessions.items():
        s.worker.recording_changed.connect(lambda active, path, key=key: records[key].append(active))
    def choose(self):
        self.directory.setText(str(shared))
        self.prefix.setText('多格式录像' if self.kind == 'video' else '录像中批量拍照')
        if self.kind == 'video':
            self.edits['demo:1'].setText('侧面 自定义名字.avi')
            self.edits['demo:1'].textEdited.emit(self.edits['demo:1'].text())
            self.format_combo.setCurrentIndex(self.format_combo.findData('.mp4'))
            assert self.edits['demo:1'].text() == '侧面 自定义名字.mp4'
        else:
            assert self.format_combo.currentData() == '.tif'
            self.format_combo.setCurrentIndex(self.format_combo.findData('.jpg'))
        self.accept()
        return self.result()
    monkeypatch.setattr(BatchSaveDialog, 'exec', choose)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(shared / '单路照片.png'), 'BMP 图片 (*.bmp)'))
    try:
        window.all_record.click()
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        paths = [s.pending_save_path for s in window.sessions.values()]
        assert all(path.endswith('.mp4') for path in paths)
        assert window.all_photo.isEnabled() and all(s.tile.photo_btn.isEnabled() for s in window.sessions.values())
        photos = window.media_format_combos['photo']
        photos.setCurrentIndex(photos.findData('.tif'))
        window.all_photo_save_as.trigger()
        until(app, lambda: len(list(shared.glob('*.jpg'))) == 2)
        window.sessions['demo:0'].tile.photo_save_as.trigger()
        until(app, lambda: (shared / '单路照片.bmp').exists())
        videos = window.media_format_combos['video']
        videos.setCurrentIndex(videos.findData('.mkv'))
        assert [s.pending_save_path for s in window.sessions.values()] == paths
        assert all(s.recording for s in window.sessions.values())
        assert records == {'demo:0': [True], 'demo:1': [True]}
        assert window.media_formats == {'photo': '.bmp', 'video': '.mkv'}
        window.all_stop.click()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        assert len(list(shared.glob('*.mp4'))) == 2
        assert not list(shared.glob('*.avi')) and not list(shared.glob('*.mkv'))
        for path in shared.glob('*.mp4'):
            assert read_video(path)[0] == (720, 1280)
    finally:
        close_window(app, window)
    restored = MainWindow(tmp_path / 'data')
    restored.show()
    try:
        assert restored.media_formats == {'photo': '.bmp', 'video': '.mkv'}
    finally:
        close_window(app, restored)
