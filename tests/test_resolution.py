"""Regress actual-driver negotiation, per-camera output sizes and rollback."""
import time

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication

import multicam.capture as backend
from multicam.capture import CameraConfig, CameraWorker
from multicam.formats import CameraMode
from multicam.ui import MainWindow
from test_capture import until, stop_all


class DriverCapture:
    """A driver whose FPS change resets FourCC and whose getters can lie."""
    def __init__(self, factory, index):
        self.factory, self.index = factory, index
        self.opened = True
        self.width, self.height, self.codec = factory.native[index][0]
        self.fps = 20

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_FPS:
            self.fps = value
            self.codec = 'YUY2'  # Mirrors the DShow graph-rebuild behavior.
        elif prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.width = int(value)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.height = int(value)
        elif prop == cv2.CAP_PROP_FOURCC:
            self.codec = int(value).to_bytes(4, 'little').decode('ascii')
        return True

    def get(self, prop):
        # A successful setter/getter must not substitute for real frame size.
        return {cv2.CAP_PROP_FRAME_WIDTH: self.width, cv2.CAP_PROP_FRAME_HEIGHT: self.height,
                cv2.CAP_PROP_FPS: self.fps}.get(prop, 0)

    def read(self):
        time.sleep(.02)
        size = (self.width, self.height, self.codec)
        if size not in self.factory.native[self.index]:
            size = self.factory.native[self.index][0]
        return True, np.full((size[1], size[0], 3), self.index * 40 + 50, np.uint8)

    def release(self):
        if self.opened:
            self.factory.releases[self.index] += 1
        self.opened = False


class DriverFactory:
    native = {0: [(1280, 720, 'YUY2'), (1920, 1080, 'MJPG')],
              1: [(800, 600, 'YUY2'), (1280, 960, 'MJPG')]}

    def __init__(self):
        self.releases = [0, 0]

    def __call__(self, index, api):
        return DriverCapture(self, index)

    def modes(self, index):
        return [CameraMode(w, h, 30, codec) for w, h, codec in self.native[index]]


def test_native_switch_other_camera_continues_and_unsupported_output_scales(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    factory = DriverFactory()
    monkeypatch.setattr(backend.cv2, 'VideoCapture', factory)
    monkeypatch.setattr(backend, 'enumerate_camera_modes', factory.modes)
    workers = [CameraWorker(0, CameraConfig(1280, 720, 20)),
               CameraWorker(1, CameraConfig(800, 600, 20))]
    changes, stats, frames, saved = [[], []], [[], []], [0, 0], []
    for n, worker in enumerate(workers):
        worker.format_changed.connect(lambda ok, config, message, n=n: changes[n].append((ok, config, message)))
        worker.stats_changed.connect(lambda value, n=n: stats[n].append(value))
        worker.snapshot_saved.connect(saved.append)
        def on_frame(image, n=n, worker=worker):
            frames[n] += 1
            worker.acknowledge_preview()
        worker.frame_ready.connect(on_frame)
        worker.start()
    try:
        until(app, lambda: all(changes) and all(stats))
        before_frames, before_releases = frames[1], factory.releases[1]
        workers[0].set_format(1920, 1080, 20, native_only=True)
        until(app, lambda: len(changes[0]) == 2)
        assert changes[0][-1][0]
        until(app, lambda: stats[0][-1]['capture_width'] == 1920 and frames[1] > before_frames)
        assert factory.releases[1] == before_releases
        assert stats[1][-1]['width'] == 800 and stats[1][-1]['height'] == 600

        workers[0].set_format(640, 360, 20)
        until(app, lambda: len(changes[0]) == 3)
        assert changes[0][-1][0] and '软件缩放' in changes[0][-1][2]
        until(app, lambda: stats[0][-1]['width'] == 640 and stats[0][-1]['scaled'])
        assert stats[0][-1]['capture_width'] == 1280
        workers[0].take_snapshot(tmp_path / 'scaled.png')
        until(app, lambda: len(saved) == 1)
        image = cv2.imdecode(np.fromfile(str(tmp_path / 'scaled.png'), np.uint8), cv2.IMREAD_COLOR)
        assert image.shape[:2] == (360, 640)

        # Strict mode must reject fallback frames even when getters claim 1024.
        workers[0].set_format(1024, 768, 20, native_only=True)
        until(app, lambda: len(changes[0]) == 4)
        assert not changes[0][-1][0] and '恢复原画面' in changes[0][-1][2]
        assert changes[0][-1][1]['width'] == 640
        assert factory.releases[1] == before_releases
        before_frames = frames[0]
        until(app, lambda: frames[0] > before_frames)
    finally:
        stop_all(app, workers)


def test_ui_keeps_each_camera_resolution_draft_and_applies_to_selected_only(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    window.show()
    try:
        window.add_demo_pair()
        until(app, lambda: all(s.ready for s in window.sessions.values()))
        window.camera_selector.setCurrentIndex(window.camera_selector.findData('demo:0'))
        window.resolution.setEditText('640 x 360')
        window.fps.setValue(15)
        window.camera_selector.setCurrentIndex(window.camera_selector.findData('demo:1'))
        window.resolution.setEditText('1280 × 960')
        window.fps.setValue(20)
        window.camera_selector.setCurrentIndex(window.camera_selector.findData('demo:0'))
        assert window._read_format().width == 640 and window._read_format().height == 360
        assert window.fps.value() == 15
        window.apply_format()
        window.camera_selector.setCurrentIndex(window.camera_selector.findData('demo:1'))
        assert window._read_format().height == 960 and window.fps.value() == 20
        assert window.sessions['demo:1'].config.height == 720  # Still unapplied.
        window.apply_format()
        until(app, lambda: not any(s.format_pending for s in window.sessions.values()))
        until(app, lambda: all(s.stats.get('height') == s.config.height for s in window.sessions.values()))
        assert (window.sessions['demo:0'].stats['width'], window.sessions['demo:0'].stats['height']) == (640, 360)
        assert (window.sessions['demo:1'].stats['width'], window.sessions['demo:1'].stats['height']) == (1280, 960)
        window.snapshot_all(prompt=False)
        until(app, lambda: len(list(window.output_dir.glob('*.png'))) == 2)
        sizes = [cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR).shape[:2]
                 for path in window.output_dir.glob('*.png')]
        assert sorted(sizes) == [(360, 640), (960, 1280)]
        window.record_all(prompt=False)
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .6)
        window.stop_all_recordings()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        video_sizes = []
        for path in window.output_dir.glob('*.avi'):
            capture = cv2.VideoCapture(str(path))
            try:
                ok, frame = capture.read()
                assert ok
                video_sizes.append(frame.shape[:2])
            finally:
                capture.release()
        assert sorted(video_sizes) == [(360, 640), (960, 1280)]
    finally:
        window.close()
        until(app, lambda: not window.isVisible())
