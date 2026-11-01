"""Exercise the date and clock inputs users click in scheduled photography."""
import time

import cv2
import numpy as np
from PySide6.QtCore import QDateTime, Qt, QPoint
from PySide6.QtTest import QTest

from multicam.timer_dialog import TimerDialog
from test_timers_zoom import simulated
from test_saving import close_window
from test_capture import until


def test_photo_date_and_clock_editable_without_unlocking_mode_and_preserved(tmp_path):
    app, window = simulated(tmp_path)
    dialog = TimerDialog(list(window.sessions.values()), None, window.output_dir, window.media_formats, window)
    try:
        dialog.kind.setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        assert dialog.start_mode.currentIndex() == 0
        assert dialog.start_at.isEnabled()
        for box in dialog.start_at.parts:
            assert box.isEnabled() and not box.isReadOnly()
        tomorrow = QDateTime.currentDateTime().addDays(1)
        dialog.start_at.setDateTime(tomorrow)
        dialog.start_mode.setCurrentIndex(0)
        for box, number in zip(dialog.start_at.parts, (18, 32, 10)):
            box.setFocus()
            QTest.keyClick(box, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(box, str(number))
            QTest.keyClick(box, Qt.Key_Tab)
            assert box.value() == number
        assert dialog.start_mode.currentIndex() == 1
        assert dialog.start_at.date.date() == tomorrow.date()
        preserved = dialog.start_at.dateTime()
        dialog.kind.setCurrentIndex(0)
        dialog.kind.setCurrentIndex(1)
        assert dialog.start_at.dateTime() == preserved
        calendar = dialog.start_at.date
        QTest.mouseClick(calendar, Qt.LeftButton, pos=QPoint(calendar.width() - 14, calendar.height() // 2))
        until(app, lambda: calendar.calendarWidget().isVisible())
        QTest.keyClick(calendar.calendarWidget(), Qt.Key_Escape)
        expected = dialog.start_at.dateTime().toMSecsSinceEpoch() / 1000
        dialog.accept()
        assert dialog.plan and dialog.plan['starts_at'] == expected
        assert dialog.start_at.dateTime().toString('HH:mm:ss') == '18:32:10'
    finally:
        dialog.close()
        close_window(app, window)


def test_immediate_mode_ignores_picked_date_and_duration_interval_buttons_work(tmp_path):
    app, window = simulated(tmp_path)
    dialog = TimerDialog(list(window.sessions.values()), None, window.output_dir, window.media_formats, window)
    try:
        dialog.show()
        dialog.kind.setCurrentIndex(1)
        dialog.start_at.setDateTime(QDateTime.currentDateTime().addDays(2))
        dialog.start_mode.setCurrentIndex(0)
        assert dialog.start_at.isEnabled()
        dialog.duration.setValue(10)
        dialog.interval.setValue(2)
        app.processEvents()
        box = dialog.duration.parts[-1]
        QTest.mouseClick(box, Qt.LeftButton, pos=QPoint(box.width() - 10, box.height() // 4))
        assert dialog.duration.value() == 11
        dialog.interval.setFocus()
        QTest.keyClick(dialog.interval, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(dialog.interval, '1.5')
        QTest.keyClick(dialog.interval, Qt.Key_Tab)
        assert dialog.interval.value() == 1.5
        assert dialog.start_mode.currentIndex() == 0
        before = time.time()
        dialog.accept()
        assert before <= dialog.plan['starts_at'] <= time.time()
        assert dialog.plan['duration'] == 11 and dialog.plan['interval'] == 1.5
    finally:
        dialog.close()
        close_window(app, window)


def test_selected_future_time_actually_delays_photos_then_saves(tmp_path):
    app, window = simulated(tmp_path)
    dialog = TimerDialog(list(window.sessions.values()), None, window.output_dir, window.media_formats, window)
    try:
        dialog.kind.setCurrentIndex(1)
        dialog.start_at.setDateTime(QDateTime.currentDateTime().addSecs(3))
        dialog.duration.setValue(1)
        dialog.interval.setValue(.4)
        dialog.accept()
        assert dialog.plan and dialog.start_mode.currentIndex() == 1
        window.create_capture_plan(**dialog.plan)
        app.processEvents()
        assert not list(window.output_dir.glob('*.png'))
        assert all(s.schedule for s in window.sessions.values())
        until(app, lambda: len(list(window.output_dir.glob('*.png'))) >= 4, timeout=8)
        until(app, lambda: not any(s.schedule or s.photo_plan for s in window.sessions.values()))
        for path in window.output_dir.glob('*.png'):
            image = cv2.imdecode(np.fromfile(str(path), np.uint8), 1)
            assert image is not None and image.shape[:2] == (240, 320)
    finally:
        dialog.close()
        close_window(app, window)
