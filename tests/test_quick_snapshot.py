"""Photo-button clicks save immediately while recordings keep running."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import cv2
import numpy as np
from PySide6.QtWidgets import QFileDialog, QMessageBox

import multicam.ui as ui
from multicam.save_dialog import BatchSaveDialog
from test_capture import until
from test_saving import close_window, demo_window


def test_quick_single_and_all_photos_never_prompt_or_overwrite_during_recording(tmp_path, monkeypatch):
    app, window = demo_window(tmp_path)
    shared = tmp_path / '快捷拍照 统一目录'
    window._remember_save_directory(shared / 'placeholder.webp')
    window.remember_media_format('photo', '.webp')
    dialogs = []
    def no_save_dialog(*args, **kwargs):
        dialogs.append('save')
        return '', ''
    def no_batch_dialog(self):
        dialogs.append('batch')
        return 0
    def no_question(*args, **kwargs):
        dialogs.append('question')
        return QMessageBox.No
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', no_save_dialog)
    monkeypatch.setattr(BatchSaveDialog, 'exec', no_batch_dialog)
    monkeypatch.setattr(QMessageBox, 'question', no_question)
    records = {key: [] for key in window.sessions}
    for key, s in window.sessions.items():
        s.worker.recording_changed.connect(lambda active, path, key=key: records[key].append(active))
    class FrozenDateTime:
        @staticmethod
        def now():
            return FrozenDateTime()
        def strftime(self, _pattern):
            return '20261030_235959_000000'
    try:
        window.record_all(prompt=False)
        until(app, lambda: all(s.recording for s in window.sessions.values()))
        video_paths = [s.pending_save_path for s in window.sessions.values()]
        monkeypatch.setattr(ui, 'datetime', FrozenDateTime)
        stamp = FrozenDateTime.now().strftime('') + '_%04d' % (window.automatic_save_sequence + 1)
        existing = window._suggested_path(window.sessions['demo:0'], 'photo', stamp)
        existing.write_bytes(b'keep existing file')
        window.sessions['demo:0'].tile.photo_btn.click()
        window.all_photo.click()
        for _ in range(3):
            window.sessions['demo:0'].tile.photo_btn.click()
        until(app, lambda: len(list(shared.glob('*.webp'))) == 7)
        assert not dialogs
        assert existing.read_bytes() == b'keep existing file'
        new_photos = [path for path in shared.glob('*.webp') if path != existing]
        assert len({path.name for path in new_photos}) == 6
        assert all(path.parent == shared for path in new_photos)
        assert any('_cam2_' in path.name for path in new_photos)
        for path in new_photos:
            image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape[:2] == (720, 1280)
        assert records == {'demo:0': [True], 'demo:1': [True]}
        assert [s.pending_save_path for s in window.sessions.values()] == video_paths
        window.all_stop.click()
        until(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()))
        assert len(list(shared.glob('*.avi'))) == 2
        assert not dialogs
    finally:
        close_window(app, window)
