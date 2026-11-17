"""User-visible layout, save decisions and stopped-camera action regressions."""
from dataclasses import asdict
import json
import sys
from threading import Event
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMessageBox, QDialog, QDialogButtonBox

from multicam.capture import CameraConfig
from multicam.save_dialog import BatchSaveDialog
from multicam.saving import SaveTarget
from multicam.timer_dialog import TimerDialog
from test_audit_regressions import app, window, wait_for, AuditWindow


@pytest.mark.parametrize('kind, selected, typed', [
    ('photo', '.png', 'manual.jpg'), ('video', '.mp4', 'manual.mkv'),
])
def test_batch_files_follow_selected_format_after_manual_name_edit(window, tmp_path, kind, selected, typed):
    s = window.sessions['demo:0']
    dialog = BatchSaveDialog([s], tmp_path, kind, window, default_format=selected)
    try:
        dialog.edits[s.key].setText(typed)
        targets = dialog.build_targets()
        assert targets[0].path.suffix == selected
        assert targets[0].path.stem == 'manual'
    finally:
        dialog.close()


def test_stopped_camera_disables_timer_zoom_and_hardware_actions(app, window):
    s = window.sessions['demo:0']
    window.zoom_available(s.key, {'supported': True, 'minimum': 100, 'maximum': 400,
                                 'step': 10, 'default': 100, 'current': 120})
    assert s.tile.zoom_apply.isEnabled()
    s.worker.shutdown()
    wait_for(app, lambda: not s.ready and not s.worker.isRunning())
    assert not s.tile.timer_btn.isEnabled()
    assert not s.tile.zoom_apply.isEnabled()
    assert not s.tile.zoom_value.isEnabled()


def test_reapplying_format_preserves_fractional_fps(app, window):
    session = window.sessions['demo:0']
    fps = 30000 / 1001
    assert window._request_format(session, CameraConfig(320, 240, fps))
    wait_for(app, lambda: not session.format_pending)
    assert window.fps.value() == 29.97
    window.apply_btn.click()
    wait_for(app, lambda: not session.format_pending)
    assert session.config.fps == fps


@pytest.mark.parametrize('success', [True, False])
def test_format_acknowledgement_preserves_newer_edits(app, window, monkeypatch, success):
    session = window.sessions['demo:0']
    started, resume = Event(), Event()
    original = session.worker._apply_format

    def delayed(config):
        started.set()
        assert resume.wait(4)
        if success:
            original(config)
        else:
            session.worker.format_changed.emit(False, asdict(session.config), '设置切换失败')

    with monkeypatch.context() as patch:
        patch.setattr(session.worker, '_apply_format', delayed)
        try:
            window.resolution.setEditText('640 × 360')
            window.apply_btn.click()
            wait_for(app, started.is_set)
            window.fps.setValue(25)
            window.resolution.setEditText('800 × ')
            resume.set()
            wait_for(app, lambda: not session.format_pending)
            assert session.config.width == (640 if success else 320)
            assert session.config.fps == 20
            assert window.resolution.currentText() == '800 × '
            assert window.fps.value() == session.draft.fps == 25
        finally:
            resume.set()

    window.resolution.setEditText('800 × 600')
    window.apply_btn.click()
    wait_for(app, lambda: not session.format_pending)
    assert session.config == CameraConfig(800, 600, 25)


def test_shared_camera_settings_merge_capture_and_individual_image_edits(app, window):
    key = 'demo:0'
    assert window._save_prefs()
    other = AuditWindow(window.data_dir)
    other.add_demo_cameras(1)
    wait_for(app, lambda: other.sessions[key].ready)
    try:
        assert window._request_format(window.sessions[key], CameraConfig(640, 360, 20))
        wait_for(app, lambda: not window.sessions[key].format_pending)
        window.sliders['contrast'][0].setValue(125)
        assert window._save_prefs()
        other.sliders['brightness'][0].setValue(25)
        assert other._save_prefs()
        stored = json.loads((window.data_dir / 'settings.json').read_text(encoding='utf-8'))['cameras'][key]
        assert stored['config'] == asdict(CameraConfig(640, 360, 20))
        assert stored['image']['brightness'] == 25 and stored['image']['contrast'] == 1.25
    finally:
        other.close()
        wait_for(app, lambda: not other.close_timer.isActive())


def test_switching_batch_format_leaves_invalid_filename_for_validation(window, tmp_path, monkeypatch):
    session = window.sessions['demo:0']
    dialog = BatchSaveDialog([session], tmp_path, 'photo', window)
    dialog.custom_names.add(session.key)
    dialog.edits[session.key].setText('.')
    raised = []
    monkeypatch.setattr(sys, 'excepthook', lambda _type, error, _stack: raised.append(error))
    try:
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData('.jpg'))
        assert not raised and dialog.edits[session.key].text() == '.'
        with pytest.raises(ValueError, match='文件名'):
            dialog.build_targets()
    finally:
        dialog.deleteLater()


def test_closed_timer_dialogs_are_deleted(window, monkeypatch):
    monkeypatch.setattr(TimerDialog, 'exec', lambda self: QDialog.Rejected)
    for _ in range(3):
        window.timer_dialog()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert window.findChildren(TimerDialog) == []


def test_auto_layout_fits_preview_width_at_normal_and_small_window_sizes(app, window):
    window.prefs['cameras'].update({'demo:%d' % i: {'config': {'width': 320, 'height': 240, 'fps': 20}}
                                  for i in range(1, 8)})
    window.resize(1450, 900)
    window.add_demo_cameras(8)
    wait_for(app, lambda: len(window.sessions) == 8 and all(s.ready for s in window.sessions.values()))
    wait_for(app, lambda: window.grid_host.width() <= window.preview_scroll.viewport().width())
    window.resize(1060, 720)
    wait_for(app, lambda: window.grid_host.width() <= window.preview_scroll.viewport().width())
    assert window.preview_scroll.horizontalScrollBar().maximum() == 0


def test_replace_confirmation_rechecks_newly_busy_path(app, window, tmp_path, monkeypatch):
    path = tmp_path / 'target.avi'
    path.write_bytes(b'previous file')
    s = window.sessions['demo:0']

    def answer(*args, **kwargs):
        # A modal confirmation dispatches events; another recording may start.
        window._start_record_one(s, SaveTarget(s.key, path, overwrite=True))
        assert s.pending_save_path == str(path)
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, 'question', answer)
    with pytest.raises(ValueError, match='正在录制'):
        window._confirm_save_targets([SaveTarget('another-camera', path)])


@pytest.mark.parametrize('batch', [False, True])
def test_zero_duration_is_rejected_before_choosing_or_remembering_save_path(window, tmp_path, monkeypatch, batch):
    original = window.output_dir
    window.duration_enabled.setChecked(True)
    window.record_duration.setValue(0)
    chosen = []
    s = window.sessions['demo:0']
    target = SaveTarget(s.key, tmp_path / 'different' / 'video.avi')
    monkeypatch.setattr(window, '_choose_single_target', lambda *args: chosen.append(True) or target)
    monkeypatch.setattr(window, '_choose_batch_targets', lambda *args: chosen.append(True) or [target])
    if batch:
        window.record_all()
    else:
        window.toggle_record(s.key)
    assert not chosen, 'invalid duration must be rejected before showing a file dialog'
    assert window.output_dir == original
    assert not s.pending_record


@pytest.mark.parametrize('batch', [False, True])
def test_duration_changed_to_zero_inside_save_dialog_does_not_change_directory(window, tmp_path, monkeypatch, batch):
    original = window.output_dir
    window.duration_enabled.setChecked(True)
    window.record_duration.setValue(1)
    s = window.sessions['demo:0']
    target = SaveTarget(s.key, tmp_path / 'different' / 'video.avi')

    def choose(*args):
        window.record_duration.setValue(0)
        return [target] if batch else target

    monkeypatch.setattr(window, '_choose_batch_targets' if batch else '_choose_single_target', choose)
    if batch:
        window.record_all()
    else:
        window.toggle_record(s.key)
    assert not s.pending_record
    assert window.output_dir == original


def test_standard_confirmation_buttons_are_chinese(window):
    buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No | QDialogButtonBox.Close)
    assert buttons.button(QDialogButtonBox.Yes).text() == '是'
    assert buttons.button(QDialogButtonBox.No).text() == '否'
    assert buttons.button(QDialogButtonBox.Close).text() == '关闭'


def test_stop_all_text_covers_photo_tasks_and_click_cancels_them(window):
    s = window.sessions['demo:0']
    window.create_capture_plan([s.key], 'photo', time.time(), 3, .5)
    assert window.all_stop.text().replace('■', '').strip() == '全部停止'
    assert '拍照' in window.all_stop.toolTip()
    window.all_stop.click()
    assert s.photo_plan is None and s.schedule is None


@pytest.mark.parametrize('output', [42, ['folder'], {'path': 'folder'}, 'bad\x00path'])
def test_invalid_stored_output_directory_falls_back_and_application_starts(app, tmp_path, output):
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'settings.json').write_text(json.dumps({'output_dir': output}), encoding='utf-8')
    window = AuditWindow(data)
    window.show()
    try:
        assert window.output_dir == data / 'captures'
        assert not window.output_custom
    finally:
        window.close()
        wait_for(app, lambda: not window.isVisible())


def test_no_tasks_disables_cancel_before_first_timer_tick(app, tmp_path):
    window = AuditWindow(tmp_path)
    try:
        assert not window.cancel_plans_btn.isEnabled()
    finally:
        window.close()
        wait_for(app, lambda: not window.closing or not window.close_timer.isActive())
