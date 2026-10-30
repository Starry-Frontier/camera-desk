"""User-selected filenames, cancelled dialogs and explicit replacement."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from multicam.capture import CameraConfig, CameraWorker
from multicam.save_dialog import BatchSaveDialog
from multicam.saving import SaveTarget, media_path, validate_targets
from multicam.ui import MainWindow
from test_capture import until, stop_all


def demo_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / 'data')
    window.show()
    window.add_demo_pair()
    until(app, lambda: all(s.ready for s in window.sessions.values()))
    return app, window


def close_window(app, window):
    window.close()
    until(app, lambda: not window.isVisible())


def test_single_save_cancel_and_named_jpeg_remembers_directory(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    initial_dir = window.output_dir
    selected = tmp_path / '中文 照片位置' / '检查 正面'
    selected.parent.mkdir()
    choices = iter([('', ''), ('', ''), (str(selected), 'JPEG 图片 (*.jpg *.jpeg)')])
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: next(choices))
    try:
        window.snapshot_one('demo:0')
        window.toggle_record('demo:0')
        assert window.output_dir == initial_dir
        assert not window.sessions['demo:0'].pending_record
        assert not window.sessions['demo:0'].recording
        assert not initial_dir.exists(), 'Cancelling must not create a capture folder'
        window.snapshot_one('demo:0')
        target = selected.with_suffix('.jpg')
        until(app, target.exists)
        image = cv2.imdecode(np.fromfile(str(target), np.uint8), cv2.IMREAD_COLOR)
        assert image.shape[:2] == (720, 1280)
        assert window.output_dir == selected.parent
        assert window.prefs['output_dir'] == str(selected.parent)
    finally:
        close_window(app, window)


def test_named_recording_keeps_previous_file_until_stop_and_stop_does_not_prompt(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    target = tmp_path / '中文 录像位置' / '现场 第一机位.avi'
    target.parent.mkdir()
    original = b'previous recording must survive until finalization'
    target.write_bytes(original)
    save_calls, replace_calls = [], []
    def save_dialog(*args, **kwargs):
        save_calls.append(args)
        return str(target), 'AVI 视频 (*.avi)'
    def confirm(*args, **kwargs):
        replace_calls.append(args)
        return QMessageBox.Yes
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save_dialog)
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    try:
        window.toggle_record('demo:0')
        until(app, lambda: window.sessions['demo:0'].recording)
        assert target.read_bytes() == original
        assert len(save_calls) == len(replace_calls) == 1
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .35)
        window.toggle_record('demo:0')
        until(app, lambda: not window.sessions['demo:0'].recording)
        assert len(save_calls) == len(replace_calls) == 1
        assert target.read_bytes() != original
        cap = cv2.VideoCapture(str(target))
        try:
            assert cap.isOpened() and cap.read()[0]
        finally:
            cap.release()
        assert window.sessions['demo:0'].pending_save_path is None
        assert window.output_dir == target.parent
    finally:
        close_window(app, window)


def test_declining_replace_or_batch_cancel_saves_nothing(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    original_dir = window.output_dir
    target = tmp_path / 'existing.png'
    target.write_bytes(b'keep original photo')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(target), 'PNG 图片 (*.png)'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.No)
    monkeypatch.setattr(BatchSaveDialog, 'exec', lambda self: QDialog.Rejected)
    try:
        window.snapshot_one('demo:0')
        window.all_photo_save_as.trigger()
        window.all_record.click()
        assert target.read_bytes() == b'keep original photo'
        assert window.output_dir == original_dir
        assert not original_dir.exists()
        assert not any(s.recording or s.pending_record for s in window.sessions.values())
        monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
        window.snapshot_one('demo:0')
        until(app, lambda: target.read_bytes() != b'keep original photo')
        replaced = cv2.imdecode(np.fromfile(str(target), np.uint8), cv2.IMREAD_COLOR)
        assert replaced is not None and replaced.shape[:2] == (720, 1280)
    finally:
        close_window(app, window)


def test_batch_common_prefix_and_per_camera_names_produce_independent_files(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    photo_dir, video_dir = tmp_path / '选定 图片', tmp_path / '选定 录像'
    calls = []
    def choose(self):
        calls.append(self.kind)
        folder = photo_dir if self.kind == 'photo' else video_dir
        suffix = '.png' if self.kind == 'photo' else '.avi'
        self.directory.setText(str(folder))
        self.prefix.setText('实验 第一组')
        self.edits['demo:1'].setText('右侧 自定义名字' + suffix)
        self.accept()  # Exercise the actual dialog validation as well.
        return self.result()
    monkeypatch.setattr(BatchSaveDialog, 'exec', choose)
    try:
        window.all_photo_save_as.trigger()
        until(app, lambda: photo_dir.exists() and len(list(photo_dir.glob('*.png'))) == 2)
        assert (photo_dir / '实验 第一组_机位01.png').exists()
        assert (photo_dir / '右侧 自定义名字.png').exists()
        window.all_record.click()
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .3)
        window.all_stop.click()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        assert calls == ['photo', 'video'], 'Stopping recordings must not ask for filenames again'
        assert (video_dir / '实验 第一组_机位01.avi').exists()
        assert (video_dir / '右侧 自定义名字.avi').exists()
        assert window.output_dir == video_dir
    finally:
        close_window(app, window)


def test_duplicate_busy_and_invalid_windows_names_are_rejected_before_io(tmp_path):
    app = QApplication.instance() or QApplication([])
    duplicate = [SaveTarget('a', tmp_path / 'SAME.avi'), SaveTarget('b', tmp_path / 'same.avi')]
    with pytest.raises(ValueError, match='同一个文件名'):
        validate_targets(duplicate)
    with pytest.raises(ValueError, match='正在录制'):
        validate_targets([duplicate[0]], [tmp_path / 'same.avi'])
    with pytest.raises(ValueError, match='同一文件夹'):
        validate_targets([SaveTarget('a', tmp_path / 'a.avi'),
                          SaveTarget('b', tmp_path / 'other' / 'b.avi')], shared_directory=tmp_path)
    for bad in ['CON.png', 'bad?.png', 'trailing .', 'LPT1.avi']:
        with pytest.raises(ValueError):
            media_path(tmp_path / bad, 'photo' if bad.endswith('.png') else 'video')
    sessions = [SimpleNamespace(key=key, number=number, device=SimpleNamespace(name=key))
                for key, number in [('a', 2), ('b', 4)]]
    dialog = BatchSaveDialog(sessions, tmp_path, 'photo')
    try:
        assert dialog.edits['a'].text().endswith('_机位02.png')
        assert dialog.edits['b'].text().endswith('_机位04.png')
        dialog.edits['a'].setText('SAME.png')
        dialog.edits['b'].setText('same.PNG')
        with pytest.raises(ValueError, match='同一个文件名'):
            dialog.build_targets()
        dialog.edits['b'].setText('../outside.png')
        with pytest.raises(ValueError):
            dialog.build_targets()
    finally:
        dialog.reject()
    assert not list(tmp_path.iterdir())


def test_failed_replacement_recording_preserves_original(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'previous.avi'
    path.write_bytes(b'original intact')
    # MJPG rejects odd dimensions; failure must not touch the accepted target.
    worker = CameraWorker('demo:0', CameraConfig(321, 241, 15))
    results = []
    worker.frame_ready.connect(lambda image: worker.acknowledge_preview())
    worker.recording_changed.connect(lambda active, name: results.append((active, name)))
    try:
        worker.start()
        worker.start_recording(path, overwrite=True)
        until(app, lambda: bool(results))
        assert results == [(False, '')]
        assert path.read_bytes() == b'original intact'
    finally:
        stop_all(app, [worker])


def test_multi_camera_defaults_to_one_shared_folder_for_photos_and_videos(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    shared = tmp_path / '多机位 共同文件夹'
    shared.mkdir()
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *args, **kwargs: str(shared))
    dialogs = []
    def keep_default_directory(self):
        # The user only edits the common name, never re-selects a camera path.
        assert Path(self.directory.text()).resolve() == shared
        dialogs.append(self.kind)
        self.prefix.setText('统一拍摄')
        self.accept()
        return self.result()
    monkeypatch.setattr(BatchSaveDialog, 'exec', keep_default_directory)
    try:
        window.choose_output()
        window.all_photo_save_as.trigger()
        until(app, lambda: len(list(shared.glob('*.png'))) == 2)
        window.all_record.click()
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        started = time.monotonic()
        until(app, lambda: time.monotonic() - started > .3)
        window.all_stop.click()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        files = list(shared.iterdir())
        assert dialogs == ['photo', 'video']
        assert {path.name for path in files} == {
            '统一拍摄_机位01.png', '统一拍摄_机位02.png',
            '统一拍摄_机位01.avi', '统一拍摄_机位02.avi'}
        assert all(path.is_file() and path.parent == shared for path in files)
    finally:
        close_window(app, window)
    restored = MainWindow(tmp_path / 'data')
    restored.show()
    try:
        assert restored.output_dir == shared
    finally:
        close_window(app, restored)
