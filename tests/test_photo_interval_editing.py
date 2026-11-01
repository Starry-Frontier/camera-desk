"""User interaction and real capture after live interval changes."""
import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox

from multicam.timer_dialog import TimerDialog, PhotoIntervalDialog
from test_timers_zoom import simulated, video_seconds
from test_saving import close_window
from test_capture import until


def type_interval(spin, text):
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Tab)


def edit_existing(app, button, text=None, expected_key=None):
    failures = []

    def edit():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, PhotoIntervalDialog)
            assert dialog.scope.currentData() == expected_key
            if text is None:
                QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Cancel), Qt.LeftButton)
            else:
                type_interval(dialog.interval, text)
                QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        except BaseException as error:
            failures.append(error)
            if dialog:
                dialog.reject()

    QTimer.singleShot(20, edit)
    QTest.mouseClick(button, Qt.LeftButton)
    if failures:
        raise failures[0]


def test_interval_is_editable_in_default_mode_and_has_real_step_buttons(tmp_path):
    app, window = simulated(tmp_path)
    dialog = TimerDialog(list(window.sessions.values()), None, window.output_dir, window.media_formats, window)
    try:
        dialog.show()
        app.processEvents()
        assert dialog.kind.currentData() == 'video'
        assert dialog.interval.isEnabled() and not dialog.interval.isReadOnly()
        QTest.mouseClick(dialog.interval_editor.plus, Qt.LeftButton)
        assert dialog.interval.value() == 5.1 and dialog.kind.currentData() == 'photo'
        dialog.kind.setCurrentIndex(0)
        QTest.mouseClick(dialog.interval_editor.minus, Qt.LeftButton)
        assert dialog.interval.value() == 5 and dialog.kind.currentData() == 'photo'
        dialog.kind.setCurrentIndex(0)
        type_interval(dialog.interval, '1.5')
        assert dialog.interval.value() == 1.5 and dialog.kind.currentData() == 'photo'
        assert '80 张照片' in dialog.estimate.text()
        dialog.interval.setValue(.1)
        QTest.mouseClick(dialog.interval_editor.minus, Qt.LeftButton)
        assert dialog.interval.value() == .1
        dialog.interval.setValue(86400)
        QTest.mouseClick(dialog.interval_editor.plus, Qt.LeftButton)
        assert dialog.interval.value() == 86400
        type_interval(dialog.interval, '2.5')
        dialog.accept()
        assert dialog.plan['interval'] == 2.5 and dialog.plan['kind'] == 'photo'
    finally:
        dialog.close()
        close_window(app, window)


def test_future_interval_edit_cancel_and_atomic_validation(tmp_path):
    app, window = simulated(tmp_path)
    a, b = list(window.sessions.values())
    try:
        assert not window.edit_intervals_btn.isEnabled()
        window.create_capture_plan([a.key, b.key], 'photo', time.time() + 120, 10, 5)
        original = [dict(s.schedule) for s in (a, b)]
        edit_existing(app, a.tile.interval_btn, '.4', a.key)
        assert a.schedule == dict(original[0], interval=.4)
        assert b.schedule == original[1]
        unchanged = [dict(s.schedule) for s in (a, b)]
        edit_existing(app, window.edit_intervals_btn)
        assert [s.schedule for s in (a, b)] == unchanged
        for value in (float('nan'), 0, 86400.1):
            with pytest.raises(ValueError):
                window.set_photo_interval([a.key, b.key], value)
        with pytest.raises(ValueError):
            window.set_photo_interval([a.key, 'missing-camera'], .2)
        assert [s.schedule for s in (a, b)] == unchanged
        edit_existing(app, window.edit_intervals_btn, '1.2')
        assert all(s.schedule['interval'] == 1.2 for s in (a, b))
        assert all(s.schedule['starts_at'] == p['starts_at'] for s, p in zip((a, b), original))
        assert not list(window.output_dir.glob('*.png'))
        window.cancel_capture_plans()
        assert not window.edit_intervals_btn.isEnabled()
        assert not a.tile.interval_btn.isEnabled()
    finally:
        close_window(app, window)


def test_live_single_and_all_intervals_change_frequency_without_restarting_video(tmp_path):
    app, window = simulated(tmp_path)
    sessions = list(window.sessions.values())
    a, b = sessions
    photos = {s.key: [] for s in sessions}
    recordings = {s.key: [] for s in sessions}
    for s in sessions:
        s.worker.snapshot_saved.connect(lambda path, key=s.key: photos[key].append(time.monotonic()))
        s.worker.recording_changed.connect(lambda active, path, key=s.key: recordings[key].append((active, path)))
    try:
        window.record_all(prompt=False)
        until(app, lambda: all(s.recording for s in sessions))
        window.create_capture_plan([s.key for s in sessions], 'photo', time.time(), 4.5, 10)
        until(app, lambda: all(photos[s.key] for s in sessions))
        original = [dict(s.photo_plan) for s in sessions]
        edit_existing(app, a.tile.interval_btn, '.2', a.key)
        assert a.photo_plan['interval'] == .2 and b.photo_plan['interval'] == 10
        until(app, lambda: len(photos[a.key]) >= 3, timeout=2)
        assert len(photos[b.key]) == 1, 'A single-camera edit must not change another camera'
        edit_existing(app, window.edit_intervals_btn, '.4')
        applied = time.monotonic()
        assert all(s.photo_plan['interval'] == .4 for s in sessions)
        assert a.photo_plan['next'] == b.photo_plan['next']
        for s, previous in zip(sessions, original):
            for field in ('end', 'duration', 'directory', 'suffix'):
                assert s.photo_plan[field] == previous[field]
            assert s.recording and [active for active, _ in recordings[s.key]] == [True]
        until(app, lambda: len(photos[b.key]) >= 2, timeout=2)
        assert .25 < photos[b.key][1] - applied < 1.5
        window.stop_all_recordings()
        until(app, lambda: all(not s.recording and not s.pending_record for s in sessions))
        for s in sessions:
            assert [active for active, _ in recordings[s.key]] == [True, False]
            assert recordings[s.key][0][1] == recordings[s.key][1][1]
        videos = list(window.output_dir.glob('*.avi'))
        assert len(videos) == 2 and all(video_seconds(path) > .5 for path in videos)
    finally:
        close_window(app, window)
