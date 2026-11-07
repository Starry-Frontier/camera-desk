"""User-visible layout, save decisions and stopped-camera action regressions."""
import json
import time

import pytest
from PySide6.QtWidgets import QMessageBox, QDialogButtonBox

from multicam.save_dialog import BatchSaveDialog
from multicam.saving import SaveTarget
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
