"""Integration coverage for independent acquisition and real saved media."""
import time

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from multicam.capture import CameraConfig, CameraWorker, ImageSettings, apply_image_settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def until(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert predicate(), "asynchronous camera operation timed out"


def stop_all(app, workers):
    for worker in workers:
        worker.shutdown()
    until(app, lambda: all(not worker.isRunning() for worker in workers))
    for worker in workers:
        assert worker.wait(1000)


def test_two_sources_save_full_size_unicode_photos_and_video(app, tmp_path):
    output = tmp_path / "中文录像目录"
    output.mkdir()
    workers = [CameraWorker("demo:%d" % n, CameraConfig(640, 360, 20)) for n in range(2)]
    frames, photos, records, errors = [[], []], [], [], []
    for n, worker in enumerate(workers):
        def on_frame(image, n=n, worker=worker):
            frames[n].append((image.width(), image.height()))
            worker.acknowledge_preview()
        worker.frame_ready.connect(on_frame)
        worker.snapshot_saved.connect(photos.append)
        worker.recording_changed.connect(lambda active, path: records.append((active, path)))
        worker.error.connect(errors.append)
    try:
        for worker in workers:
            worker.start()
        until(app, lambda: all(len(f) >= 3 for f in frames))
        for n, worker in enumerate(workers):
            worker.take_snapshot(output / ("机位%d.png" % n))
            worker.start_recording(output / ("机位%d.avi" % n))
        until(app, lambda: len(photos) == 2 and sum(int(active) for active, _ in records) == 2)
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > 1.15)
        for worker in workers:
            worker.stop_recording()
        until(app, lambda: len([v for v in records if not v[0]]) == 2)
        assert not errors
        for n in range(2):
            picture = cv2.imdecode(np.fromfile(str(output / ("机位%d.png" % n)), np.uint8), cv2.IMREAD_COLOR)
            assert picture.shape == (360, 640, 3)
            capture = cv2.VideoCapture(str(output / ("机位%d.avi" % n)))
            try:
                assert capture.isOpened()
                duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / capture.get(cv2.CAP_PROP_FPS)
                assert 1.0 <= duration <= 2.1
                ok, frame = capture.read()
                assert ok and frame.shape == (360, 640, 3)
            finally:
                capture.release()
        # Reconfiguration of one camera must not stop the other one.
        workers[0].set_format(1280, 720, 15)
        changed = []
        workers[0].stats_changed.connect(changed.append)
        frames_before = len(frames[1])
        until(app, lambda: any(s["width"] == 1280 and s["height"] == 720 for s in changed))
        workers[0].take_snapshot(output / "分辨率变更.png")
        until(app, lambda: len(photos) == 3 and len(frames[1]) > frames_before)
        full = cv2.imdecode(np.fromfile(str(output / "分辨率变更.png"), np.uint8), cv2.IMREAD_COLOR)
        assert full.shape[:2] == (720, 1280)
    finally:
        stop_all(app, workers)


def test_preview_is_bounded_and_recording_continues_without_preview_credit(app, tmp_path):
    worker = CameraWorker("demo:2", CameraConfig(320, 240, 15))
    frames, saved, errors = [], [], []
    worker.frame_ready.connect(frames.append)
    worker.snapshot_saved.connect(saved.append)
    worker.error.connect(errors.append)
    try:
        worker.start()
        until(app, lambda: len(frames) == 1)
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .25)
        assert len(frames) == 1, "previews must not build an unbounded Qt signal backlog"
        worker.take_snapshot(tmp_path / "still.png")
        until(app, lambda: bool(saved))
        assert not errors
        worker.acknowledge_preview()
        until(app, lambda: len(frames) == 2)
    finally:
        stop_all(app, [worker])


def test_recording_refuses_resolution_changes_and_never_overwrites(app, tmp_path):
    worker = CameraWorker("demo:0", CameraConfig(320, 240, 10))
    records, errors, stats = [], [], []
    worker.frame_ready.connect(lambda image: worker.acknowledge_preview())
    worker.recording_changed.connect(lambda active, path: records.append((active, path)))
    worker.error.connect(errors.append)
    worker.stats_changed.connect(stats.append)
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep this file")
    try:
        worker.start()
        until(app, lambda: len(stats) > 0)
        worker.take_snapshot(existing)
        until(app, lambda: any("已存在" in error for error in errors))
        assert existing.read_bytes() == b"keep this file"
        worker.start_recording(tmp_path / "video.avi")
        until(app, lambda: bool(records) and records[-1][0])
        worker.set_format(640, 480, 20)
        until(app, lambda: any("停止录像" in error for error in errors))
        assert stats[-1]["width"] == 320
    finally:
        stop_all(app, [worker])
    assert (tmp_path / "video.avi").is_file(), "shutdown must finish the AVI index"


def test_pixel_transform_identity_orientation_and_real_contrast():
    image = np.array([[[20, 40, 60], [90, 110, 130]], [[150, 170, 190], [210, 230, 250]]], np.uint8)
    assert np.array_equal(apply_image_settings(image, ImageSettings()), image)
    assert np.array_equal(apply_image_settings(image, ImageSettings(mirror=True)), image[:, ::-1])
    assert np.array_equal(apply_image_settings(image, ImageSettings(flip_vertical=True)), image[::-1])
    black = apply_image_settings(image, ImageSettings(brightness=-100))
    assert not black.any()
    contrasted = apply_image_settings(image, ImageSettings(contrast=2))
    assert contrasted[0, 0, 0] == 0 and contrasted[-1, -1, -1] == 255
    grey = apply_image_settings(image, ImageSettings(saturation=0))
    assert np.array_equal(grey[:, :, 0], grey[:, :, 1])
    assert np.array_equal(grey[:, :, 1], grey[:, :, 2])


def test_invalid_formats_are_rejected_before_native_code():
    with pytest.raises(ValueError):
        CameraWorker("demo:0", CameraConfig(0, 480, 30))
    with pytest.raises(ValueError):
        CameraWorker("demo:0", CameraConfig(640, 480, float("nan")))


def test_slow_acquisition_retains_real_recording_duration(app, tmp_path, monkeypatch):
    import multicam.capture as backend
    original = backend._demo_frame

    def slow_frame(*args):
        time.sleep(.13)  # About 7 fps arriving for a 30 fps recording.
        return original(*args)

    monkeypatch.setattr(backend, "_demo_frame", slow_frame)
    worker = CameraWorker("demo:0", CameraConfig(320, 240, 30))
    records, errors = [], []
    worker.frame_ready.connect(lambda image: worker.acknowledge_preview())
    worker.error.connect(errors.append)
    worker.recording_changed.connect(lambda active, path: records.append((active, time.monotonic())))
    try:
        worker.start()
        worker.start_recording(tmp_path / "slow.avi")
        until(app, lambda: bool(records))
        until(app, lambda: time.monotonic() - records[0][1] > 1.0)
        worker.stop_recording()
        until(app, lambda: len(records) == 2)
        assert not errors
        elapsed = records[1][1] - records[0][1]
        capture = cv2.VideoCapture(str(tmp_path / "slow.avi"))
        try:
            seconds = capture.get(cv2.CAP_PROP_FRAME_COUNT) / capture.get(cv2.CAP_PROP_FPS)
            assert abs(seconds - elapsed) < .15, "dropped capture frames must not accelerate video"
        finally:
            capture.release()
    finally:
        stop_all(app, [worker])


def test_failed_recording_never_announces_saved_path(app, tmp_path):
    worker = CameraWorker("demo:0", CameraConfig(320, 240, 15))
    records, errors = [], []
    worker.frame_ready.connect(lambda image: worker.acknowledge_preview())
    worker.error.connect(errors.append)
    worker.recording_changed.connect(lambda active, path: records.append((active, path)))
    existing = tmp_path / "existing.avi"
    existing.write_bytes(b"keep previous video")
    try:
        worker.start()
        worker.start_recording(existing)
        until(app, lambda: len(records) == 1)
        assert records == [(False, "")]
        assert errors and "已存在" in errors[0]
        assert existing.read_bytes() == b"keep previous video"
    finally:
        stop_all(app, [worker])

