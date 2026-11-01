"""Visible selection and stepping controls, including in styled Windows builds."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QWidget, QHBoxLayout, QSizePolicy


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
