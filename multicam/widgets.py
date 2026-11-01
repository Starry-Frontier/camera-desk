"""Visible selection and stepping controls, including in styled Windows builds."""
from PySide6.QtCore import QPoint, Qt, QDateTime, QTime, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox, QAbstractSpinBox, QDateTimeEdit, QDateEdit, QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy, QPushButton


def selection_arrow(widget):
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#246448' if widget.isEnabled() else '#a9b1ac'), 2))
    x, y = widget.width() - 15, widget.height() // 2
    painter.drawPolyline([QPoint(x - 4, y - 2), QPoint(x, y + 2), QPoint(x + 4, y - 2)])
    painter.end()


class SelectionComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.setMinimumWidth(80)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def paintEvent(self, event):
        super().paintEvent(event)
        selection_arrow(self)


class SelectionDateTimeEdit(QDateTimeEdit):
    def paintEvent(self, event):
        super().paintEvent(event)
        selection_arrow(self)


class SelectionDateEdit(QDateEdit):
    def paintEvent(self, event):
        super().paintEvent(event)
        selection_arrow(self)


class StepButtons:
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor('#246448' if self.isEnabled() else '#a9b1ac'), 1.5))
        x, top, bottom = self.width() - 11, self.height() // 4, self.height() * 3 // 4
        painter.drawLine(x - 3, top, x + 3, top)
        painter.drawLine(x, top - 3, x, top + 3)
        painter.drawLine(x - 3, bottom, x + 3, bottom)
        painter.end()


class NumberBox(StepButtons, QSpinBox):
    pass


class DecimalBox(StepButtons, QDoubleSpinBox):
    pass


class PhotoIntervalEdit(QWidget):
    """An editable seconds field with separate, unambiguous step buttons."""
    def __init__(self, value=5, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(.1, 86400)
        self.spin.setDecimals(1)
        self.spin.setSingleStep(.1)
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setKeyboardTracking(False)
        self.spin.setSuffix(' 秒 / 张')
        self.spin.setValue(value)
        self.spin.setAccessibleName('拍照间隔（秒）')
        self.spin.setToolTip('直接输入每张照片之间的秒数，范围 0.1～86400 秒。')
        row.addWidget(self.spin, 1)
        self.minus = QPushButton('−')
        self.plus = QPushButton('+')
        for widget, name in ((self.minus, '减少拍照间隔'), (self.plus, '增加拍照间隔')):
            widget.setFixedWidth(38)
            widget.setAutoDefault(False)
            widget.setAccessibleName(name)
            widget.setToolTip(name + '（每次 0.1 秒）')
            row.addWidget(widget)
        self.minus.clicked.connect(self.spin.stepDown)
        self.plus.clicked.connect(self.spin.stepUp)
        self.setFocusProxy(self.spin)


class DateTimePicker(QWidget):
    """A calendar and explicit clock fields, rather than a date-only popup."""
    dateTimeChanged = Signal(QDateTime)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.date = SelectionDateEdit(value.date())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat('yyyy-MM-dd')
        self.date.setAccessibleName('预约拍照或录像的开始日期')
        layout.addWidget(self.date)
        row = QHBoxLayout()
        self.parts = []
        for title, maximum in [('时', 23), ('分', 59), ('秒', 59)]:
            box = NumberBox()
            box.setRange(0, maximum)
            box.setSuffix(' ' + title)
            box.setMinimumWidth(70)
            box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            box.setAccessibleName('预约开始时间（%s）' % title)
            row.addWidget(box)
            self.parts.append(box)
        self.hours, self.minutes, self.seconds = self.parts
        layout.addLayout(row)
        self.setDateTime(value)
        self.date.dateChanged.connect(self.changed)
        for box in self.parts:
            box.valueChanged.connect(self.changed)

    def dateTime(self):
        return QDateTime(self.date.date(), QTime(*(box.value() for box in self.parts)))

    def setDateTime(self, value):
        # Atomic update: never advertise an intermediate, possibly past time.
        controls = [self.date] + self.parts
        blocked = [control.blockSignals(True) for control in controls]
        try:
            self.date.setDate(value.date())
            for box, number in zip(self.parts, (value.time().hour(), value.time().minute(), value.time().second())):
                box.setValue(number)
        finally:
            for control, previous in zip(controls, blocked):
                control.blockSignals(previous)
        self.dateTimeChanged.emit(self.dateTime())

    def changed(self, *_):
        self.dateTimeChanged.emit(self.dateTime())


class DurationEdit(QWidget):
    def __init__(self, seconds=60, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.parts = []
        for title, maximum in [('时', 167), ('分', 59), ('秒', 59)]:
            box = NumberBox()
            box.setRange(0, maximum)
            box.setSuffix(' ' + title)
            box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            box.setMinimumWidth(66)
            box.setAccessibleName('拍摄时长（%s）' % title)
            row.addWidget(box)
            self.parts.append(box)
        self.setValue(seconds)

    def value(self):
        return self.parts[0].value() * 3600 + self.parts[1].value() * 60 + self.parts[2].value()

    def setValue(self, seconds):
        try:
            seconds = max(0, min(604799, int(seconds)))
        except (ValueError, TypeError, OverflowError):
            seconds = 60
        for box, value in zip(self.parts, (seconds // 3600, seconds // 60 % 60, seconds % 60)):
            box.setValue(value)
