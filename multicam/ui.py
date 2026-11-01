from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QUrl, QPoint
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QVBoxLayout, QWidget, QToolButton, QMenu,
)

from .capture import CameraConfig, CameraWorker, ImageSettings
from .devices import CameraDevice, enumerate_cameras
from . import __version__
from .save_dialog import BatchSaveDialog
from .saving import SaveTarget, media_path, path_identity, validate_targets
from .media_formats import change_suffix, format_for_extension, format_for_filter, formats_for
from .widgets import SelectionComboBox as QComboBox, NumberBox as QSpinBox, DurationEdit
from .timer_dialog import TimerDialog, PhotoIntervalDialog


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #24322e; }
QMainWindow, QWidget#root { background: #f2f4f1; }
QFrame#header, QFrame#panel { background: #ffffff; border: 1px solid #e0e5df; border-radius: 12px; }
QLabel#brand { font-size: 22px; font-weight: 700; color: #183e31; }
QLabel#muted { color: #78847e; font-size: 12px; }
QLabel#section { font-weight: 600; color: #78847e; font-size: 11px; }
QLabel#heading { font-size: 16px; font-weight: 600; }
QPushButton { background: #ffffff; border: 1px solid #dbe2dc; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { background: #edf3ee; border-color: #a0baaa; }
QPushButton:pressed { background: #dceade; }
QPushButton:disabled { color: #a9b1ac; background: #f3f5f2; border-color: #e6eae5; }
QPushButton#primary { color: white; background: #246448; border-color: #246448; font-weight: 600; }
QPushButton#primary:hover { background: #1c523a; }
QPushButton#primary:disabled { background: #b7cbbd; border-color: #b7cbbd; }
QPushButton#record { background: #a83e37; color: white; border-color: #a83e37; }
QPushButton#record:disabled { background: #dbc1bd; border-color: #dbc1bd; }
QToolButton { background: #ffffff; border: 1px solid #dbe2dc; border-radius: 7px; padding: 8px 22px 8px 12px; }
QToolButton:hover { background: #edf3ee; border-color: #a0baaa; }
QToolButton:pressed { background: #dceade; }
QToolButton:disabled { color: #a9b1ac; background: #f3f5f2; border-color: #e6eae5; }
QToolButton::menu-button { width: 17px; border-left: 1px solid #dbe2dc; }
QMenu { background: #ffffff; border: 1px solid #dbe2dc; padding: 5px; }
QMenu::item { padding: 7px 16px; }
QMenu::item:selected { background: #edf3ee; }
QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QLineEdit { background: #fafbf9; border: 1px solid #dbe2dc; border-radius: 6px; padding: 6px; min-height: 20px; }
QComboBox, QDateTimeEdit { padding-right: 32px; }
QComboBox::drop-down, QDateTimeEdit::drop-down { border-left: 1px solid #c6d0c7; width: 28px; background: #e6f0e7; border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QComboBox::down-arrow, QDateTimeEdit::down-arrow { image: none; }
QSpinBox, QDoubleSpinBox { padding-right: 4px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { width: 20px; background: #e6f0e7; border-left: 1px solid #c6d0c7; }
QSpinBox::down-button, QDoubleSpinBox::down-button { width: 20px; background: #e6f0e7; border-left: 1px solid #c6d0c7; }
QSpinBox::up-arrow, QSpinBox::down-arrow, QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { image: none; }
QComboBox QAbstractItemView { background: white; selection-background-color: #dcebdd; color: #24322e; }
QCheckBox { spacing: 7px; padding: 4px 0; }
QCheckBox::indicator { width: 16px; height: 16px; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background: #e1e7df; }
QSlider::sub-page:horizontal { background: #4a8863; border-radius: 2px; }
QSlider::handle:horizontal { background: #246448; width: 13px; height: 13px; margin: -5px 0; border-radius: 6px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 7px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #c6d0c7; border-radius: 3px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QFrame#tile { background: #ffffff; border: 2px solid #e0e5df; border-radius: 10px; }
QFrame#tile[selected="true"] { border: 2px solid #36805a; }
QLabel#tiletitle { font-weight: 600; }
QLabel#badge { color: #397452; background: #e6f0e7; border-radius: 5px; padding: 4px 8px; font-size: 11px; }
QLabel#notice { color: #64726a; padding: 5px; }
QFrame#divider { background: #e6ebe4; max-height: 1px; border: none; }
"""


def label(text, name=None, wrap=False):
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    result.setWordWrap(wrap)
    return result


def button(text, callback, name=None):
    result = QPushButton(text)
    if name:
        result.setObjectName(name)
    result.clicked.connect(callback)
    return result


def photo_button(text, quick_capture, save_as):
    result = QToolButton()
    result.setText(text)
    result.setPopupMode(QToolButton.MenuButtonPopup)
    result.clicked.connect(quick_capture)
    menu = QMenu(result)
    save_as_action = menu.addAction('拍照并另存为…', save_as)
    result.setMenu(menu)
    return result, save_as_action


def app_icon():
    pixmap = QPixmap(128, 128)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#246448'))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, 128, 128, 28, 28)
    painter.setBrush(QColor('#e4f0dc'))
    painter.drawRoundedRect(23, 33, 68, 62, 10, 10)
    painter.drawPolygon([QPoint(94, 49), QPoint(112, 39), QPoint(112, 89), QPoint(94, 79)])
    painter.setBrush(QColor('#246448'))
    painter.drawEllipse(39, 46, 36, 36)
    painter.setBrush(QColor('#d4df98'))
    painter.drawEllipse(51, 58, 12, 12)
    painter.end()
    return QIcon(pixmap)


class DeviceScan(QThread):
    found = Signal(list, str)

    def run(self):
        try:
            self.found.emit(enumerate_cameras(), '')
        except Exception as exc:
            self.found.emit([], str(exc))


class VideoView(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.message = '正在连接摄像头…'
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(640, 360)

    def set_image(self, image):
        self.image = image
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#111d19'))
        if not self.image.isNull():
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            x, y = (self.width() - size.width()) // 2, (self.height() - size.height()) // 2
            p.drawImage(x, y, self.image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            p.setPen(QColor('#8eaaa0'))
            p.setFont(QFont('Microsoft YaHei UI', 11))
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, self.message)
        p.end()

    def mousePressEvent(self, event):
        self.clicked.emit()


class CameraTile(QFrame):
    selected_signal = Signal()
    snapshot = Signal()
    snapshot_save_as = Signal()
    record = Signal()
    remove = Signal()
    timer_requested = Signal()
    interval_requested = Signal()
    zoom_requested = Signal(int)

    def __init__(self, number, name):
        super().__init__()
        self.setObjectName('tile')
        self.setProperty('selected', False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        head = QHBoxLayout()
        head.addWidget(label('%02d' % number, 'badge'))
        self.title = label(name, 'tiletitle')
        self.title.setToolTip(name)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(self.title, 1)
        self.timer_btn = button('定时', self.timer_requested.emit)
        head.addWidget(self.timer_btn)
        self.interval_btn = button('间隔', self.interval_requested.emit)
        self.interval_btn.setEnabled(False)
        head.addWidget(self.interval_btn)
        self.close_btn = button('×', self.remove.emit)
        self.close_btn.setFixedSize(27, 27)
        self.close_btn.setStyleSheet('padding: 0; font-size: 18px;')
        self.close_btn.setToolTip('断开此机位（会先保存正在录制的视频）')
        head.addWidget(self.close_btn)
        layout.addLayout(head)
        self.view = VideoView()
        self.view.clicked.connect(self.selected_signal.emit)
        layout.addWidget(self.view, 1)
        footer = QHBoxLayout()
        self.info = label('等待画面', 'muted')
        footer.addWidget(self.info, 1)
        self.photo_btn, self.photo_save_as = photo_button('拍照', self.snapshot.emit, self.snapshot_save_as.emit)
        self.photo_btn.setToolTip('点击直接保存到预设目录；右侧箭头可另存为。录像会继续进行。')
        self.rec_btn = button('● 录制', self.record.emit)
        self.photo_btn.setEnabled(False)
        self.rec_btn.setEnabled(False)
        footer.addWidget(self.photo_btn)
        footer.addWidget(self.rec_btn)
        layout.addLayout(footer)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(label('硬件变焦', 'muted'))
        self.zoom_value = QSpinBox()
        self.zoom_value.setAccessibleName('此机位硬件变焦值')
        self.zoom_apply = button('应用', lambda: self.zoom_requested.emit(self.zoom_value.value()))
        self.zoom_value.setEnabled(False)
        self.zoom_apply.setEnabled(False)
        zoom_row.addWidget(self.zoom_value, 1)
        zoom_row.addWidget(self.zoom_apply)
        layout.addLayout(zoom_row)
        self.zoom_hint = label('正在读取设备变焦能力…', 'muted', True)
        layout.addWidget(self.zoom_hint)

    def set_zoom(self, caps):
        supported = bool(caps.get('supported'))
        self.zoom_value.setEnabled(supported)
        self.zoom_apply.setEnabled(supported)
        if supported:
            self.zoom_value.setRange(caps['minimum'], caps['maximum'])
            self.zoom_value.setSingleStep(caps['step'])
            value = caps.get('current')
            self.zoom_value.setValue(value if value is not None else caps['default'])
            self.zoom_hint.setText('设备值 %d～%d · 步长 %d%s' % (caps['minimum'], caps['maximum'], caps['step'],
                                      ' · 当前值未知' if value is None else ''))
        else:
            self.zoom_hint.setText(caps.get('reason', '设备不支持硬件变焦'))

    def mousePressEvent(self, event):
        self.selected_signal.emit()

    def select(self, selected):
        self.setProperty('selected', selected)
        self.style().unpolish(self)
        self.style().polish(self)


class CameraSession:
    def __init__(self, key, device, worker, tile, config, image, number=0):
        self.key, self.device, self.worker, self.tile = key, device, worker, tile
        self.config, self.image = config, image
        self.number = number
        self.stats = {}
        self.ready = False
        self.recording = False
        self.pending_record = False
        self.record_action = None
        self.pending_save_path = None
        self.removing = False
        self.last_error = ''
        self.format_pending = False
        self.draft = CameraConfig(**asdict(config))
        self.modes = []
        self.zoom_caps = {'supported': False}
        self.schedule = None
        self.photo_plan = None


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, demo=False, demo_count=2):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.prefs = self._load_prefs()
        self.media_formats = {}
        self.media_format_combos = {}
        for kind, default in [('photo', '.png'), ('video', '.avi')]:
            try:
                self.media_formats[kind] = format_for_extension(kind, self.prefs.get(kind + '_format', default)).suffix
            except ValueError:
                self.media_formats[kind] = default
        stored_output = self.prefs.get('output_dir')
        self.output_custom = bool(stored_output)
        self.output_dir = Path(stored_output) if stored_output else self.data_dir / 'captures'
        output_unavailable = self.output_custom and not self.output_dir.is_dir()
        if output_unavailable:
            self.output_dir = self.data_dir / 'captures'
            self.output_custom = False
        self.sessions: Dict[str, CameraSession] = {}
        self.selected: Optional[str] = None
        self.scan = None
        self.closing = False
        self.loading = False
        self.sequence = 0
        self.automatic_save_sequence = 0
        self.setWindowTitle('Camera Desk · 多机位摄像台 v' + __version__)
        self.setWindowIcon(app_icon())
        self.resize(1450, 900)
        self.setMinimumSize(1060, 720)
        self.setStyleSheet(STYLE)
        self._build()
        if output_unavailable:
            self.notify('原保存位置不可用，已切换到软件数据目录下的 captures 文件夹。')
        self.close_timer = QTimer(self)
        self.close_timer.setInterval(100)
        self.close_timer.timeout.connect(self._finish_close)
        self.capture_timer = QTimer(self)
        self.capture_timer.setTimerType(Qt.PreciseTimer)
        self.capture_timer.setInterval(100)
        self.capture_timer.timeout.connect(self.tick_capture_plans)
        self.capture_timer.start()
        QTimer.singleShot(0, self.refresh_devices)
        if demo:
            QTimer.singleShot(150, lambda: self.add_demo_cameras(demo_count))

    def _load_prefs(self):
        try:
            prefs = json.loads((self.data_dir / 'settings.json').read_text(encoding='utf-8'))
            return prefs if isinstance(prefs, dict) else {}
        except (OSError, ValueError):
            return {}

    def _build(self):
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(20, 18, 20, 12)
        page.setSpacing(16)
        header = QFrame()
        header.setObjectName('header')
        h = QHBoxLayout(header)
        h.setContentsMargins(20, 14, 20, 14)
        title = QVBoxLayout()
        title.setSpacing(3)
        title.addWidget(label('Camera Desk', 'brand'))
        title.addWidget(label('多机位摄像台  /  每一个角度，都在掌握', 'muted'))
        h.addLayout(title)
        h.addStretch()
        self.count_label = label('0 个机位', 'badge')
        h.addWidget(self.count_label)
        h.addSpacing(14)
        h.addWidget(button('打开保存目录', self.open_output))
        self.all_photo, self.all_photo_save_as = photo_button(
            '全部拍照', lambda: self.snapshot_all(prompt=False), lambda: self.snapshot_all(prompt=True))
        self.all_photo_save_as.setText('全部拍照并另存为…')
        self.all_photo.setToolTip('点击将所有机位照片直接保存到同一预设目录；右侧箭头可另存为。')
        self.all_record = button('● 全部开始录像', self.record_all, 'primary')
        self.all_stop = button('■ 全部停止录像', self.stop_all_recordings, 'record')
        h.addWidget(self.all_photo)
        h.addWidget(self.all_record)
        h.addWidget(self.all_stop)
        page.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(12)
        page.addWidget(splitter, 1)
        left = QFrame()
        left.setObjectName('panel')
        left.setMinimumWidth(210)
        left.setMaximumWidth(300)
        sidebar = QVBoxLayout(left)
        sidebar.setContentsMargins(16, 20, 16, 18)
        sidebar.setSpacing(12)
        sidebar.addWidget(label('设备连接', 'heading'))
        sidebar.addWidget(label('USB 摄像头 / 免驱采集卡', 'muted'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumContentsLength(12)
        self.device_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        sidebar.addWidget(self.device_combo)
        self.add_btn = button('+ 添加机位', self.add_selected_device, 'primary')
        sidebar.addWidget(self.add_btn)
        self.refresh_btn = button('刷新设备', self.refresh_devices)
        sidebar.addWidget(self.refresh_btn)
        self.device_hint = label('正在查找设备…', 'muted', True)
        sidebar.addWidget(self.device_hint)
        sidebar.addSpacing(8)
        divider = QFrame()
        divider.setObjectName('divider')
        sidebar.addWidget(divider)
        sidebar.addWidget(label('预览布局', 'section'))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(['自动布局', '双列布局', '单列布局', '只看选中机位'])
        self.layout_combo.currentIndexChanged.connect(self.relayout)
        sidebar.addWidget(self.layout_combo)
        sidebar.addWidget(label('点击画面，调整该机位的参数。', 'muted', True))
        sidebar.addWidget(button('定时拍摄 / 存储预估…', lambda: self.timer_dialog()))
        self.edit_intervals_btn = button('调整拍照间隔…', lambda: self.photo_interval_dialog())
        sidebar.addWidget(self.edit_intervals_btn)
        self.plans_label = label('暂无预约或间隔拍照任务', 'muted', True)
        sidebar.addWidget(self.plans_label)
        self.cancel_plans_btn = button('取消定时任务', self.cancel_capture_plans)
        self.cancel_plans_btn.setToolTip('取消预约和间隔拍照；已开始的录像仍可用录制／停止按钮结束。')
        sidebar.addWidget(self.cancel_plans_btn)
        sidebar.addStretch()
        sidebar.addWidget(label('保存位置', 'section'))
        self.output_label = label(str(self.output_dir), 'muted', True)
        sidebar.addWidget(self.output_label)
        sidebar.addWidget(button('更改保存位置', self.choose_output))
        format_form = QFormLayout()
        for kind, title in [('photo', '图片格式'), ('video', '视频格式')]:
            combo = QComboBox()
            combo.setMinimumContentsLength(6)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            for spec in formats_for(kind):
                combo.addItem('%s (%s)' % (spec.label.split()[0], spec.suffix), spec.suffix)
            combo.setCurrentIndex(combo.findData(self.media_formats[kind]))
            combo.setToolTip('用于下次保存；不会改变正在录制的文件格式。')
            combo.currentIndexChanged.connect(lambda _index, kind=kind: self.remember_media_format(kind, self.media_format_combos[kind].currentData()))
            self.media_format_combos[kind] = combo
            format_form.addRow(title, combo)
        sidebar.addLayout(format_form)
        sidebar.addSpacing(10)
        sidebar.addWidget(button('体验多机位演示', self.add_demo_pair))
        sidebar.addWidget(button('使用说明', self.show_help))
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setMinimumWidth(260)
        sidebar_scroll.setMaximumWidth(280)
        sidebar_scroll.setWidget(left)
        splitter.addWidget(sidebar_scroll)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(label('实时监看', 'heading'))
        row.addStretch()
        row.addWidget(label('原比例预览 · 独立视频文件', 'muted'))
        center_layout.addLayout(row)
        self.empty = QFrame()
        self.empty.setObjectName('panel')
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.addStretch()
        empty_layout.addWidget(label('把两个角度，放进一个工作台', 'heading'), alignment=Qt.AlignCenter)
        empty_layout.addWidget(label('连接摄像头后，从左侧添加机位。\n也可先用演示画面体验拍照与录制。', 'muted'), alignment=Qt.AlignCenter)
        empty_layout.addSpacing(15)
        empty_layout.addWidget(button('开始多机位演示', self.add_demo_pair, 'primary'), alignment=Qt.AlignCenter)
        empty_layout.addStretch()
        center_layout.addWidget(self.empty, 1)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.preview_scroll.setWidget(self.grid_host)
        self.preview_scroll.hide()
        center_layout.addWidget(self.preview_scroll, 1)
        center_layout.addWidget(label('录像时可同时拍照；图片和视频格式可在左侧选择，录像不含声音。', 'muted'))
        splitter.addWidget(center)

        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(310)
        inspector_scroll.setMaximumWidth(350)
        right = QFrame()
        right.setObjectName('panel')
        self.inspector_layout = QVBoxLayout(right)
        self.inspector_layout.setContentsMargins(18, 20, 18, 18)
        self.inspector_layout.setSpacing(12)
        self.inspector_layout.addWidget(label('画面设置', 'heading'))
        self.selected_label = label('添加或选中一个机位', 'muted', True)
        self.inspector_layout.addWidget(self.selected_label)
        self.camera_selector = QComboBox()
        self.camera_selector.setToolTip('选择需要设置的摄像头')
        self.camera_selector.currentIndexChanged.connect(self.camera_selection_changed)
        self.inspector_layout.addWidget(self.camera_selector)
        self.controls = QWidget()
        control = QVBoxLayout(self.controls)
        control.setContentsMargins(0, 0, 0, 0)
        control.setSpacing(12)
        control.addWidget(label('输出分辨率 / 帧率', 'section'))
        self.resolution = QComboBox()
        self.resolution.setEditable(True)
        self.resolution.setInsertPolicy(QComboBox.NoInsert)
        self.resolution.setToolTip('选择分辨率，或输入宽 × 高，例如 1280 × 960')
        self.resolution.currentTextChanged.connect(self.format_draft_changed)
        control.addWidget(self.resolution)
        self.format_hint = label('读取设备格式后将更新选项，也可输入宽 × 高。', 'muted', True)
        control.addWidget(self.format_hint)
        self.native_only = QCheckBox('仅相机原生分辨率（禁止缩放）')
        self.native_only.setToolTip('不勾选时：优先原生切换；设备不支持时按输出尺寸缩放。')
        self.native_only.toggled.connect(self.format_draft_changed)
        control.addWidget(self.native_only)
        fr = QHBoxLayout()
        fr.addWidget(label('目标帧率'))
        self.fps = QSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setSuffix(' fps')
        self.fps.valueChanged.connect(self.format_draft_changed)
        fr.addWidget(self.fps, 1)
        control.addLayout(fr)
        self.apply_btn = button('应用采集格式', self.apply_format)
        control.addWidget(self.apply_btn)
        self.actual_label = label('实际格式将在画面到达后显示', 'muted', True)
        control.addWidget(self.actual_label)
        self.duration_enabled = QCheckBox('录像达到时长后自动停止')
        control.addWidget(self.duration_enabled)
        self.record_duration = DurationEdit(self.prefs.get('record_duration', 60))
        control.addWidget(self.record_duration)
        self.duration_enabled.setChecked(bool(self.prefs.get('duration_enabled', False)))
        self.record_duration.setEnabled(self.duration_enabled.isChecked())
        self.duration_enabled.toggled.connect(self.record_duration.setEnabled)
        control.addWidget(label('此设置用于下次开始的录像；每路独立计时。', 'muted', True))
        control.addWidget(label('图像调整', 'section'))
        self.sliders = {}
        for name, title, lo, hi, default in [('brightness', '亮度', -100, 100, 0),
                                           ('contrast', '对比度', 0, 300, 100),
                                           ('saturation', '饱和度', 0, 300, 100),
                                           ('sharpness', '锐度', 0, 300, 0)]:
            row = QHBoxLayout()
            row.addWidget(label(title))
            value = label(str(default), 'muted')
            row.addStretch()
            row.addWidget(value)
            control.addLayout(row)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
            slider.valueChanged.connect(lambda v, n=name, target=value: self.slider_changed(n, v, target))
            self.sliders[name] = (slider, value)
            control.addWidget(slider)
        self.mirror = QCheckBox('水平镜像')
        self.flip = QCheckBox('垂直翻转')
        self.mirror.toggled.connect(self.image_changed)
        self.flip.toggled.connect(self.image_changed)
        row = QHBoxLayout()
        row.addWidget(self.mirror)
        row.addWidget(self.flip)
        control.addLayout(row)
        control.addWidget(button('还原图像调整', self.reset_image))
        control.addWidget(button('设备高级参数…', self.hardware_dialog))
        control.addWidget(label('高级参数是否生效，取决于摄像头支持。\n录制时暂不能切换分辨率。', 'muted', True))
        self.inspector_layout.addWidget(self.controls)
        self.controls.setEnabled(False)
        self.inspector_layout.addStretch()
        inspector_scroll.setWidget(right)
        splitter.addWidget(inspector_scroll)
        splitter.setSizes([260, 800, 330])
        splitter.setStretchFactor(1, 1)
        self.notice = label('就绪 · 图像仅在本机处理与保存', 'notice', True)
        page.addWidget(self.notice)
        self._update_actions()

    def notify(self, message):
        self.notice.setText(message)
        self.notice.setToolTip(message)

    def refresh_devices(self):
        if self.scan is not None and self.scan.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.device_hint.setText('正在查找设备…')
        self.scan = DeviceScan(self)
        self.scan.found.connect(self.devices_found)
        self.scan.start()

    def devices_found(self, devices, error):
        self.refresh_btn.setEnabled(True)
        self.device_combo.clear()
        for device in devices:
            self.device_combo.addItem('%s  [%d]' % (device.name, device.index), device)
        self.add_btn.setEnabled(bool(devices))
        self.device_hint.setText(error or ('发现 %d 个视频设备' % len(devices) if devices else '未发现摄像头。连接后点击刷新；请允许 Windows 桌面应用访问摄像头。'))

    def add_selected_device(self):
        device = self.device_combo.currentData()
        if device:
            self.add_camera(device)

    def add_demo_pair(self):
        self.add_demo_cameras(2)

    def add_demo_cameras(self, count=2):
        if not 1 <= count <= 8:
            raise ValueError('模拟机位数量须为 1 至 8。')
        for n in range(count):
            self.add_camera(CameraDevice(n, '演示机位 %s · 模拟画面' % chr(65 + n), 'demo:%d' % n), demo=True)

    def add_camera(self, device, demo=False):
        key = device.device_id
        if key in self.sessions:
            self.select_session(key)
            return
        saved = self.prefs.get('cameras', {}).get(key, {})
        try:
            config = CameraConfig(**{k: v for k, v in saved.get('config', {}).items() if k in ('width', 'height', 'fps', 'native_only')})
            image = ImageSettings(**{k: v for k, v in saved.get('image', {}).items() if k in asdict(ImageSettings())})
        except (TypeError, ValueError):
            config, image = CameraConfig(), ImageSettings()
        self.sequence += 1
        tile = CameraTile(self.sequence, device.name)
        worker = CameraWorker(key if demo else device.index, config, self)
        session = CameraSession(key, device, worker, tile, config, image, self.sequence)
        self.sessions[key] = session
        self.camera_selector.blockSignals(True)
        self.camera_selector.addItem('%02d · %s' % (self.sequence, device.name), key)
        self.camera_selector.blockSignals(False)
        tile.selected_signal.connect(lambda: self.select_session(key))
        tile.snapshot.connect(lambda: self.snapshot_one(key, prompt=False))
        tile.snapshot_save_as.connect(lambda: self.snapshot_one(key, prompt=True))
        tile.record.connect(lambda: self.toggle_record(key))
        tile.remove.connect(lambda: self.remove_camera(key))
        tile.timer_requested.connect(lambda: self.timer_for_camera(key))
        tile.interval_requested.connect(lambda: self.photo_interval_dialog(key))
        tile.zoom_requested.connect(lambda value: worker.set_hardware('zoom', value))
        # A removed camera may have queued signals when its index is reused.
        def guarded(callback):
            return lambda *args: callback(*args) if self.sessions.get(key) is session else None
        worker.frame_ready.connect(tile.view.set_image)
        worker.frame_ready.connect(guarded(lambda _: self.frame_arrived(key)))
        worker.stats_changed.connect(guarded(lambda stats: self.stats_arrived(key, stats)))
        worker.formats_available.connect(guarded(lambda modes: self.formats_arrived(key, modes)))
        worker.format_changed.connect(guarded(lambda ok, config, message: self.format_applied(key, ok, config, message)))
        worker.zoom_available.connect(guarded(lambda caps: self.zoom_available(key, caps)))
        worker.hardware_changed.connect(guarded(lambda name, value, ok: self.hardware_feedback(key, name, value, ok)))
        worker.error.connect(guarded(lambda message: self.camera_error(key, message)))
        worker.status_changed.connect(guarded(lambda message: self.camera_status(key, message)))
        worker.snapshot_saved.connect(guarded(lambda path: self.notify('照片已保存：' + path)))
        worker.recording_changed.connect(guarded(lambda recording, path: self.recording_changed(key, recording, path)))
        worker.finished.connect(guarded(lambda: self.worker_finished(key)))
        worker.update_image(image)
        worker.start()
        self.select_session(key)
        self.relayout()

    def frame_arrived(self, key):
        s = self.sessions.get(key)
        if s:
            s.worker.acknowledge_preview()
        if s and not s.ready:
            s.ready = True
            s.tile.photo_btn.setEnabled(True)
            s.tile.rec_btn.setEnabled(True)
            self._update_actions()

    def stats_arrived(self, key, stats):
        s = self.sessions.get(key)
        if not s:
            return
        s.stats = stats
        text = '%s × %s · %.1f fps' % (stats.get('width', '—'), stats.get('height', '—'), stats.get('fps', 0))
        if s.recording:
            seconds = int(stats.get('recording_seconds', 0))
            text += '   ● %02d:%02d' % (seconds // 60, seconds % 60)
            remaining = stats.get('recording_remaining')
            if remaining is not None:
                text += ' · 剩余 %d 秒' % math.ceil(remaining)
        if s.schedule:
            text += ' · 预约 ' + datetime.fromtimestamp(s.schedule['starts_at']).strftime('%H:%M:%S')
        if s.photo_plan:
            text += ' · 每 %g 秒拍照' % s.photo_plan['interval']
        elif s.schedule and s.schedule['kind'] == 'photo':
            text += ' · 间隔 %g 秒' % s.schedule['interval']
        s.tile.info.setText(text)
        if self.selected == key:
            self.actual_label.setText(self.format_description(stats))

    def zoom_available(self, key, caps):
        s = self.sessions.get(key)
        if s:
            s.zoom_caps = dict(caps)
            s.tile.set_zoom(caps)

    def hardware_feedback(self, key, name, value, ok):
        s = self.sessions.get(key)
        if name != 'zoom' or not s:
            return
        if value is not None:
            s.zoom_caps['current'] = value
            s.tile.zoom_value.setValue(int(value))
        self.notify('%s：%s' % (s.device.name, '变焦已生效（设备值 %s）' % value if ok else '设备未确认变焦结果，请查看画面或检查设备。'))

    def timer_dialog(self, key=None):
        if not self.sessions:
            self.notify('请先添加机位，再设置定时拍摄。')
            return
        dialog = TimerDialog(list(self.sessions.values()), key, self.output_dir, dict(self.media_formats), self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.create_capture_plan(**dialog.plan)
        except ValueError as exc:
            QMessageBox.warning(self, '无法创建拍摄任务', str(exc))

    def timer_for_camera(self, key):
        s = self.sessions.get(key)
        if s and (s.schedule or s.photo_plan):
            s.schedule = s.photo_plan = None
            self.tick_capture_plans()
            self.notify('%s：已取消此机位的预约或间隔拍照，录像可继续。' % s.device.name)
        else:
            self.timer_dialog(key)

    @staticmethod
    def photo_capture_plan(session):
        return session.photo_plan or (session.schedule if session.schedule and session.schedule['kind'] == 'photo' else None)

    def photo_interval_dialog(self, key=None):
        sessions = [s for s in self.sessions.values() if self.photo_capture_plan(s)]
        if not sessions or (key is not None and not any(s.key == key for s in sessions)):
            self.notify('暂无可调整的拍照任务，请先创建间隔拍照任务。')
            return
        dialog = PhotoIntervalDialog(sessions, key, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.set_photo_interval(**dialog.plan)
        except ValueError as exc:
            QMessageBox.warning(self, '无法调整拍照间隔', str(exc))

    def set_photo_interval(self, keys, interval):
        keys = list(dict.fromkeys(keys))
        interval = float(interval)
        if not keys or not math.isfinite(interval) or not .1 <= interval <= 86400:
            raise ValueError('请选择拍照任务，间隔须为 0.1～86400 秒。')
        now = time.monotonic()
        chosen = []
        for key in keys:
            s = self.sessions.get(key)
            plan = self.photo_capture_plan(s) if s else None
            if (not plan or not self._can_save(s) or not s.worker.isRunning()
                    or (s.photo_plan is plan and now >= plan['end'])):
                raise ValueError('所选拍照任务已结束、已取消或机位不可用，请重新选择。')
            chosen.append((s, plan))
        # Validate the complete selection before changing any task.
        for s, plan in chosen:
            plan['interval'] = interval
            if s.photo_plan is plan:
                plan['next'] = now + interval
        self.tick_capture_plans()
        self.notify('已将 %d 路拍照间隔改为 %g 秒；原结束时间不变，录像继续。' % (len(chosen), interval))

    def create_capture_plan(self, keys, kind, starts_at, duration, interval=5):
        keys = list(dict.fromkeys(keys))
        if kind not in ('video', 'photo') or not keys:
            raise ValueError('请选择拍摄方式和机位。')
        if not all(math.isfinite(float(v)) for v in (starts_at, duration, interval)) or not 0 < duration <= 604800 or interval < .1:
            raise ValueError('拍摄时长须大于 0 且不超过 7 天，拍照间隔须至少 0.1 秒。')
        chosen = [self.sessions.get(key) for key in keys]
        for s in chosen:
            if s is None or not self._can_save(s) or not s.worker.isRunning() or s.schedule or (kind == 'photo' and s.photo_plan):
                raise ValueError('所选机位未就绪或已有定时任务，请先取消该任务。')
            if kind == 'video' and not self._can_start_recording(s):
                raise ValueError('所选机位正在录像，请停止后再创建录像任务；间隔拍照可在录像中运行。')
        targets = self._automatic_targets(chosen, 'video') if kind == 'video' else []
        for index, s in enumerate(chosen):
            s.schedule = {'kind': kind, 'starts_at': float(starts_at), 'duration': float(duration),
                          'interval': float(interval), 'directory': Path(self.output_dir),
                          'suffix': self.media_formats[kind],
                          'target': targets[index] if kind == 'video' else None}
        self.notify('已创建 %d 个机位的%s任务。' % (len(chosen), '录像' if kind == 'video' else '间隔拍照'))
        self.tick_capture_plans()

    def tick_capture_plans(self):
        if self.closing:
            return
        wall, now = time.time(), time.monotonic()
        for s in list(self.sessions.values()):
            plan = s.schedule
            if plan and wall >= plan['starts_at']:
                s.schedule = None
                if wall - plan['starts_at'] > 60 or not self._can_save(s) or not s.worker.isRunning():
                    self.notify('%s：预约已错过或机位不可用，任务已取消。' % s.device.name)
                elif plan['kind'] == 'video':
                    self._start_record_one(s, plan['target'], plan['duration'])
                else:
                    s.photo_plan = dict(plan, end=now + plan['duration'], next=now)
            plan = s.photo_plan
            if plan:
                if now >= plan['end'] or not self._can_save(s) or not s.worker.isRunning():
                    s.photo_plan = None
                elif now >= plan['next']:
                    target = self._automatic_targets([s], 'photo', directory=plan['directory'], suffix=plan['suffix'])[0]
                    s.worker.take_snapshot(target.path)
                    plan['next'] += max(1, math.floor((now - plan['next']) / plan['interval']) + 1) * plan['interval']
            if s.schedule or s.photo_plan:
                self.stats_arrived(s.key, s.stats)
        scheduled = sum(s.schedule is not None for s in self.sessions.values())
        photos = sum(s.photo_plan is not None for s in self.sessions.values())
        self.plans_label.setText('预约：%d 路 · 间隔拍照：%d 路' % (scheduled, photos) if scheduled or photos else '暂无预约或间隔拍照任务')
        self.cancel_plans_btn.setEnabled(bool(scheduled or photos))
        self._update_actions()

    def cancel_capture_plans(self, checked=False):
        for s in self.sessions.values():
            s.schedule = s.photo_plan = None
        self.tick_capture_plans()
        self.notify('预约和间隔拍照任务已取消。')

    @staticmethod
    def format_description(stats):
        text = '输出：%s × %s · %.1f fps' % (stats.get('width', '—'), stats.get('height', '—'), stats.get('fps', 0))
        text += '\n设备采集：%s × %s' % (stats.get('capture_width', stats.get('width', '—')), stats.get('capture_height', stats.get('height', '—')))
        return text + (' · 软件缩放' if stats.get('scaled') else ' · 原生尺寸')

    def camera_error(self, key, message):
        s = self.sessions.get(key)
        if s:
            s.last_error = message
            logging.warning('%s: %s', s.device.name, message)
            s.tile.rec_btn.setEnabled(s.ready and s.worker.isRunning())
            if not s.ready:
                s.tile.view.message = message + '\n\n可关闭此机位，检查连接后重新添加。'
                s.tile.view.update()
            self.notify(s.device.name + '：' + message)
        self._update_actions()

    def camera_status(self, key, message):
        s = self.sessions.get(key)
        if s and not (s.last_error and message in ('连接异常', '已停止')):
            self.notify('%s：%s' % (s.device.name, message))

    def recording_changed(self, key, recording, path):
        s = self.sessions.get(key)
        if not s:
            return
        s.recording = recording
        # A stop may be requested while this worker is still starting. Its late
        # start acknowledgement must not discard the queued stop or unlock it.
        if not recording or s.record_action != 'stop':
            s.pending_record = False
            s.record_action = None
        s.pending_save_path = path if recording else None
        if not recording and path:
            self.notify((s.last_error + '；已保存录像：' if s.last_error else '视频已保存：') + path)
        elif recording and path:
            self.notify(s.device.name + ' 正在录制：' + path)
        if key == self.selected:
            self.apply_btn.setEnabled(not recording)
        self._update_actions()

    def worker_finished(self, key):
        s = self.sessions.get(key)
        if not s:
            return
        s.ready = False
        s.schedule = s.photo_plan = None
        s.recording = False
        s.pending_record = False
        s.record_action = None
        s.pending_save_path = None
        s.format_pending = False
        s.tile.photo_btn.setEnabled(False)
        s.tile.rec_btn.setEnabled(False)
        if s.removing:
            self._discard_session(key)
        else:
            s.tile.info.setText('已断开 · 关闭此机位后可重新添加')
        self._update_actions()

    def remove_camera(self, key):
        s = self.sessions.get(key)
        if not s:
            return
        self._remember(s)
        s.removing = True
        s.schedule = s.photo_plan = None
        s.tile.setEnabled(False)
        s.worker.shutdown()
        if not s.worker.isRunning():
            self._discard_session(key)
        else:
            self.notify('正在保存视频并释放 ' + s.device.name + '…')

    def _discard_session(self, key):
        s = self.sessions.pop(key, None)
        if not s:
            return
        self.grid.removeWidget(s.tile)
        self.camera_selector.blockSignals(True)
        index = self.camera_selector.findData(key)
        if index >= 0:
            self.camera_selector.removeItem(index)
        self.camera_selector.blockSignals(False)
        s.tile.deleteLater()
        s.worker.deleteLater()
        if key == self.selected:
            self.selected = None
            self.select_session(next(iter(self.sessions), None))
        self.relayout()

    def relayout(self, *_):
        while self.grid.count():
            self.grid.takeAt(0)
        mode, count = self.layout_combo.currentIndex(), len(self.sessions)
        cols = 2 if mode == 1 else 1 if mode in (2, 3) else (1 if count <= 2 else 2 if count <= 4 else 3)
        visible = [s for s in self.sessions.values() if mode != 3 or s.key == self.selected]
        for s in self.sessions.values():
            s.tile.setVisible(s in visible)
        for i, s in enumerate(visible):
            self.grid.addWidget(s.tile, i // cols, i % cols)
        for i in range(20):
            self.grid.setRowStretch(i, 1 if i < math.ceil(len(visible) / cols) else 0)
            self.grid.setColumnStretch(i, 1 if i < cols else 0)
        self.empty.setVisible(not count)
        self.preview_scroll.setVisible(bool(count))
        self.count_label.setText('%d 个机位' % count)
        self._update_actions()

    def select_session(self, key):
        self.selected = key
        s = self.sessions.get(key)
        self.loading = True
        self.controls.setEnabled(bool(s))
        for item in self.sessions.values():
            item.tile.select(item.key == key)
        if s:
            self.camera_selector.blockSignals(True)
            self.camera_selector.setCurrentIndex(self.camera_selector.findData(key))
            self.camera_selector.blockSignals(False)
            self.selected_label.setText(s.device.name)
            self.fill_resolution_options(s)
            self.fps.setValue(int(s.draft.fps))
            self.native_only.setChecked(s.draft.native_only)
            self.apply_btn.setEnabled(s.ready and not s.recording and not s.pending_record and not s.format_pending)
            self.apply_btn.setText('正在切换…' if s.format_pending else '应用到此机位')
            for name, (slider, value) in self.sliders.items():
                slider.setValue(round(getattr(s.image, name) * (1 if name == 'brightness' else 100)))
                value.setText(str(slider.value()) if name == 'brightness' else '%d%%' % slider.value())
            self.mirror.setChecked(s.image.mirror)
            self.flip.setChecked(s.image.flip_vertical)
            self.actual_label.setText(self.format_description(s.stats))
        else:
            self.selected_label.setText('添加或选中一个机位')
        self.loading = False
        if self.layout_combo.currentIndex() == 3:
            self.relayout()

    def camera_selection_changed(self, *_):
        if not self.loading:
            key = self.camera_selector.currentData()
            if key in self.sessions:
                self.select_session(key)

    def fill_resolution_options(self, s):
        sizes = {(m['width'], m['height']) for m in s.modes}
        reported = set(sizes)
        sizes.update([(320, 240), (640, 480), (800, 600), (1280, 720),
                      (1280, 960), (1920, 1080), (2560, 1440), (3840, 2160)])
        sizes.add((s.draft.width, s.draft.height))
        self.resolution.clear()
        for w, h in sorted(sizes, key=lambda size: (size[0] * size[1], size)):
            self.resolution.addItem('%d × %d' % (w, h), (w, h))
            if (w, h) in reported:
                self.resolution.setItemData(self.resolution.count() - 1, '此设备报告的格式', Qt.ToolTipRole)
        selected_index = next(i for i in range(self.resolution.count())
                              if tuple(self.resolution.itemData(i)) == (s.draft.width, s.draft.height))
        self.resolution.setCurrentIndex(selected_index)
        self.format_hint.setText(('设备报告 %d 种采集尺寸；可选择其他输出尺寸或手动输入。' % len(reported))
                                 if reported else '可选择输出尺寸或输入宽 × 高；会显示是否使用软件缩放。')

    def _read_format(self):
        match = re.fullmatch(r'\s*(\d+)\s*[×xX*]\s*(\d+)\s*', self.resolution.currentText())
        if not match:
            raise ValueError('请按“宽 × 高”输入分辨率，例如 1280 × 960。')
        return CameraConfig(int(match[1]), int(match[2]), self.fps.value(), self.native_only.isChecked()).validated()

    def format_draft_changed(self, *_):
        s = self.sessions.get(self.selected)
        if self.loading or not s:
            return
        try:
            s.draft = self._read_format()
        except ValueError:
            pass  # A partially typed custom resolution is not submitted.

    def formats_arrived(self, key, modes):
        s = self.sessions.get(key)
        if s:
            s.modes = modes
            if self.selected == key:
                self.loading = True
                self.fill_resolution_options(s)
                self.loading = False

    def format_applied(self, key, ok, config, message):
        s = self.sessions.get(key)
        if not s:
            return
        was_pending = s.format_pending
        s.format_pending = False
        previous = s.config
        s.config = CameraConfig(**config)
        # On failure restore the confirmed format, not an unaccepted request.
        if was_pending or not ok or asdict(s.draft) == asdict(previous):
            s.draft = CameraConfig(**config)
        if ok:
            s.last_error = ''
            self._remember(s)
            self.notify(s.device.name + '：' + (message or '已切换至 %d × %d' % (s.config.width, s.config.height)))
        elif message:
            self.notify(s.device.name + '：' + message)
        if self.selected == key:
            self.select_session(key)
        self._update_actions()

    def slider_changed(self, name, value, target):
        target.setText(str(value) if name == 'brightness' else '%d%%' % value)
        self.image_changed()

    def image_changed(self, *_):
        s = self.sessions.get(self.selected)
        if self.loading or not s:
            return
        s.image = ImageSettings(**{name: slider.value() / (1 if name == 'brightness' else 100) for name, (slider, _) in self.sliders.items()}, mirror=self.mirror.isChecked(), flip_vertical=self.flip.isChecked())
        s.worker.update_image(s.image)
        self._remember(s)

    def reset_image(self):
        s = self.sessions.get(self.selected)
        if s:
            s.image = ImageSettings()
            s.worker.update_image(s.image)
            self._remember(s)
            self.select_session(s.key)

    def apply_format(self):
        s = self.sessions.get(self.selected)
        if not s or not s.ready or s.recording or s.pending_record or s.format_pending:
            return
        try:
            requested = self._read_format()
        except ValueError as exc:
            self.notify(str(exc))
            return
        s.draft = requested
        s.format_pending = True
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText('正在切换…')
        s.tile.info.setText('正在切换采集格式…')
        s.worker.set_format(requested.width, requested.height, requested.fps, requested.native_only)
        self.notify('正在为 %s 切换至 %d × %d，其他机位继续运行。' % (s.device.name, requested.width, requested.height))
        self._update_actions()

    def _suggested_path(self, s, kind, stamp=None, directory=None, suffix=None):
        stamp = stamp or datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in s.device.name)[:45]
        suffix = suffix or self.media_formats[kind]
        return (directory if directory is not None else self.output_dir) / ('%s_cam%d_%s%s' % (stamp, s.number, name, suffix))

    def _confirm_save_targets(self, targets):
        busy = [s.pending_save_path for s in self.sessions.values() if s.pending_save_path]
        validate_targets(targets, busy)
        existing = [target.path for target in targets if target.path.exists()]
        if existing:
            names = '\n'.join(str(path) for path in existing[:6])
            if len(existing) > 6:
                names += '\n另有 %d 个文件' % (len(existing) - 6)
            answer = QMessageBox.question(
                self, '确认替换文件',
                '以下文件已经存在，是否替换？\n\n' + names + '\n\n原文件会在新照片或录像成功保存后才被替换。',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return None
        confirmed = {path_identity(path) for path in existing}
        return [replace(target, overwrite=path_identity(target.path) in confirmed) for target in targets]

    def _choose_single_target(self, s, kind):
        suggested = str(self._suggested_path(s, kind))
        filters = ';;'.join(spec.file_filter for spec in formats_for(kind))
        preferred = format_for_extension(kind, self.media_formats[kind]).file_filter
        title = ('保存照片 · ' if kind == 'photo' else '设置录像保存位置 · ') + s.device.name
        while True:
            raw_path, selected_filter = QFileDialog.getSaveFileName(
                self, title, suggested, filters, selectedFilter=preferred, options=QFileDialog.DontConfirmOverwrite)
            if not raw_path:
                return None
            suggested = raw_path
            try:
                spec = format_for_filter(kind, selected_filter)
                if spec:
                    raw_path = change_suffix(raw_path, kind, spec.suffix)
                path = media_path(raw_path, kind, self.media_formats[kind])
                targets = self._confirm_save_targets([SaveTarget(s.key, path)])
                if targets:
                    self.remember_media_format(kind, path.suffix)
                    return targets[0]
                return None
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, '请检查保存位置', str(exc))

    def _choose_batch_targets(self, sessions, kind):
        dialog = BatchSaveDialog(sessions, self.output_dir, kind, self, default_format=self.media_formats[kind])
        if kind == 'video' and any(s.recording or s.pending_record for s in self.sessions.values()):
            dialog.setWindowTitle('开始其余 %d 个机位录像 · 现有录像继续' % len(sessions))
        if dialog.exec() != QDialog.Accepted:
            return None
        try:
            validate_targets(dialog.targets, shared_directory=dialog.directory.text().strip())
            targets = self._confirm_save_targets(dialog.targets)
            if targets:
                self.remember_media_format(kind, dialog.format_combo.currentData())
            return targets
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '请检查保存位置', str(exc))
            return None

    def _automatic_targets(self, sessions, kind, directory=None, suffix=None):
        directory = self.output_dir.resolve()
        # Counter plus existence checks also cover rapid clicks or a system
        # clock moved backwards; quick capture never asks to overwrite a file.
        while True:
            self.automatic_save_sequence += 1
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_%04d' % self.automatic_save_sequence
            targets = [SaveTarget(s.key, self._suggested_path(s, kind, stamp, directory, suffix)) for s in sessions]
            if not any(target.path.exists() for target in targets):
                break
        return validate_targets(targets, shared_directory=directory)

    def _remember_save_directory(self, path):
        self.output_dir = Path(path).parent
        self.output_custom = self.output_dir.resolve() != (self.data_dir / 'captures').resolve()
        self.output_label.setText(str(self.output_dir))
        self._save_prefs()

    def remember_media_format(self, kind, suffix):
        spec = format_for_extension(kind, suffix)
        self.media_formats[kind] = spec.suffix
        combo = self.media_format_combos.get(kind)
        if combo is not None:
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(spec.suffix))
            combo.blockSignals(False)
        self._save_prefs()

    def _can_save(self, s):
        return self.sessions.get(s.key) is s and s.ready and not s.removing and not s.format_pending

    def snapshot_one(self, key, *, prompt=True):
        s = self.sessions.get(key)
        if not s or not self._can_save(s):
            return
        target = self._choose_single_target(s, 'photo') if prompt else self._automatic_targets([s], 'photo')[0]
        if target and self._can_save(s):
            s.worker.take_snapshot(target.path, overwrite=target.overwrite)
            self.notify('正在保存照片，录像继续进行。' if s.recording else '正在保存照片…')
            if prompt:
                self._remember_save_directory(target.path)

    def snapshot_all(self, checked=False, *, prompt=True):
        sessions = [s for s in self.sessions.values() if self._can_save(s)]
        if not sessions:
            return
        targets = self._choose_batch_targets(sessions, 'photo') if prompt else self._automatic_targets(sessions, 'photo')
        if not targets:
            return
        planned = {s.key: s for s in sessions}
        saved = []
        for target in targets:
            s = planned.get(target.key)
            if s and self._can_save(s):
                s.worker.take_snapshot(target.path, overwrite=target.overwrite)
                saved.append(target)
        if prompt and saved:
            self._remember_save_directory(saved[0].path)
        if saved:
            self.notify('正在保存各机位照片，录像继续进行。' if any(s.recording for s in sessions) else '正在保存各机位照片…')

    def _can_start_recording(self, s):
        return self._can_save(s) and not s.recording and not s.pending_record and not (s.schedule and s.schedule['kind'] == 'video')

    def _start_record_one(self, s, target, duration_seconds=None):
        if not self._can_start_recording(s):
            return
        if duration_seconds is None:
            duration_seconds = self.record_duration.value() if self.duration_enabled.isChecked() else 0
            if self.duration_enabled.isChecked() and duration_seconds <= 0:
                self.notify('请先设置大于 0 的录像时长。')
                return
        s.last_error = ''
        s.pending_save_path = str(target.path)
        s.pending_record = True
        s.record_action = 'start'
        s.worker.start_recording(target.path, overwrite=target.overwrite, duration_seconds=duration_seconds)
        self._update_actions()

    def _stop_record_one(self, s):
        if self.sessions.get(s.key) is not s or s.removing or s.record_action == 'stop':
            return
        if not s.recording and s.record_action != 'start':
            return
        s.pending_record = True
        s.record_action = 'stop'
        s.worker.stop_recording()
        self._update_actions()

    def toggle_record(self, key, *, prompt=True):
        s = self.sessions.get(key)
        if not s:
            return
        if s.recording or s.record_action == 'start':
            self._stop_record_one(s)
            return
        if s.schedule and s.schedule['kind'] == 'video':
            s.schedule = None
            self.tick_capture_plans()
            return
        if not self._can_start_recording(s):
            return
        target = self._choose_single_target(s, 'video') if prompt else self._automatic_targets([s], 'video')[0]
        if target and self._can_start_recording(s):
            self._start_record_one(s, target)
            if prompt:
                self._remember_save_directory(target.path)

    def record_all(self, checked=False, *, prompt=True):
        sessions = [s for s in self.sessions.values() if self._can_start_recording(s)]
        if not sessions:
            return
        targets = self._choose_batch_targets(sessions, 'video') if prompt else self._automatic_targets(sessions, 'video')
        if not targets:
            return
        planned = {s.key: s for s in sessions}
        started = []
        for target in targets:
            s = planned.get(target.key)
            if s and self._can_start_recording(s):
                self._start_record_one(s, target)
                started.append(target)
        if prompt and started:
            self._remember_save_directory(started[0].path)

    def stop_all_recordings(self, checked=False):
        self.cancel_capture_plans()
        for s in list(self.sessions.values()):
            self._stop_record_one(s)

    def _update_actions(self):
        ready = any(s.ready and not s.removing and not s.format_pending for s in self.sessions.values())
        idle = any(self._can_start_recording(s) for s in self.sessions.values())
        busy = any(s.recording or s.pending_record for s in self.sessions.values())
        can_stop = any(not s.removing and s.record_action != 'stop' and
                       (s.recording or s.record_action == 'start') for s in self.sessions.values())
        self.all_photo.setEnabled(ready)
        self.all_record.setEnabled(idle)
        self.all_record.setText('● 开始其余机位' if idle and busy else '● 全部开始录像')
        self.all_stop.setEnabled(can_stop or any(s.schedule or s.photo_plan for s in self.sessions.values()))
        self.edit_intervals_btn.setEnabled(any(self.photo_capture_plan(s) for s in self.sessions.values()))
        count = sum(s.recording for s in self.sessions.values())
        self.count_label.setText('%d 个机位%s' % (len(self.sessions), ' · %d 路录像' % count if count else ''))
        for s in self.sessions.values():
            s.tile.photo_btn.setEnabled(s.ready and not s.removing and not s.format_pending)
            s.tile.timer_btn.setText('取消定时' if s.schedule or s.photo_plan else '定时')
            photo_plan = self.photo_capture_plan(s)
            s.tile.interval_btn.setEnabled(bool(photo_plan) and not s.removing)
            s.tile.interval_btn.setToolTip('当前每 %g 秒拍照，点击调整此机位间隔。' % photo_plan['interval']
                                         if photo_plan else '创建间隔拍照任务后，可在这里调整间隔。')
            if s.schedule and s.schedule['kind'] == 'video' and not s.recording:
                s.tile.rec_btn.setText('取消预约')
                s.tile.rec_btn.setEnabled(True)
            elif s.record_action == 'stop':
                s.tile.rec_btn.setText('正在保存…')
                s.tile.rec_btn.setEnabled(False)
            elif s.record_action == 'start':
                s.tile.rec_btn.setText('■ 取消开始')
                s.tile.rec_btn.setEnabled(not s.removing)
            else:
                s.tile.rec_btn.setText('■ 停止' if s.recording else '● 录制')
                s.tile.rec_btn.setEnabled(self._can_save(s))
            object_name = 'record' if s.recording or s.pending_record else ''
            if s.tile.rec_btn.objectName() != object_name:
                s.tile.rec_btn.setObjectName(object_name)
                s.tile.rec_btn.style().unpolish(s.tile.rec_btn)
                s.tile.rec_btn.style().polish(s.tile.rec_btn)
        s = self.sessions.get(self.selected)
        if s:
            self.apply_btn.setEnabled(s.ready and not s.recording and not s.pending_record and not s.format_pending)

    def hardware_dialog(self):
        s = self.sessions.get(self.selected)
        if not s:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('设备高级参数 · ' + s.device.name)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('此处直接向摄像头发送参数。不同设备的\n取值范围可能不同，不支持时会在主窗口提示。', 'muted', True))
        form = QFormLayout()
        prop = QComboBox()
        for title, key in [('自动曝光', 'auto_exposure'), ('曝光值', 'exposure'), ('自动对焦', 'autofocus'), ('自动白平衡', 'auto_white_balance'), ('白平衡色温', 'white_balance')]:
            prop.addItem(title, key)
        value = QSpinBox()
        value.setRange(-10000, 20000)
        value.setValue(1)
        form.addRow('参数', prop)
        form.addRow('数值', value)
        layout.addLayout(form)
        layout.addWidget(label('自动功能：1 开启，0 关闭。\nWindows UVC 曝光常见值为 −13 至 −1；\n手动曝光前请先关闭自动曝光。', 'muted', True))
        send = button('发送到此机位', lambda: s.worker.set_hardware(prop.currentData(), value.value()), 'primary')
        layout.addWidget(send)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, '选择照片与视频保存目录', str(self.output_dir))
        if path:
            self.output_dir = Path(path)
            self.output_custom = True
            self.output_label.setText(path)
            self._save_prefs()

    def open_output(self):
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))
        except OSError as exc:
            self.notify('无法打开保存目录：' + str(exc))

    def show_help(self):
        QMessageBox.information(self, '使用说明', '1. 连接摄像头后逐个添加机位，在右侧分别设置画面参数。\n2. 每路可独立录制／停止，也可全部开始／全部停止。\n3. 已有录像时，全部开始只补开其余机位，原录像继续。\n4. “拍照／全部拍照”直接保存，不弹窗，录像期间同样可用。\n\n照片使用左侧预设目录和图片格式，以时间、序号及机位编号命名。\n需要手动命名时，点击拍照按钮右侧箭头选择“另存为”。\n多路照片默认共用一个文件夹；开始录像仍可选择位置及名称。\n\n图片：PNG/JPEG/BMP/TIFF/WebP；视频：AVI/MP4/MKV。\n左侧格式只影响下次保存，不改变当前录像。快捷拍照不覆盖旧照片；\n另存为同名文件时确认后才替换，停止录像直接完成保存。\n\n设备不支持所选原生尺寸时可使用软件缩放，界面分别显示采集\n与输出尺寸。各路为独立无声视频，不提供硬件帧同步。')

    def _remember(self, s):
        cameras = self.prefs.setdefault('cameras', {})
        cameras[s.key] = {'config': asdict(s.config), 'image': asdict(s.image)}

    def _save_prefs(self):
        for s in self.sessions.values():
            self._remember(s)
        # Default output follows the portable data folder on any computer.
        self.prefs['output_dir'] = str(self.output_dir) if self.output_custom else None
        self.prefs['photo_format'] = self.media_formats['photo']
        self.prefs['video_format'] = self.media_formats['video']
        self.prefs['duration_enabled'] = self.duration_enabled.isChecked()
        self.prefs['record_duration'] = self.record_duration.value()
        try:
            target = self.data_dir / 'settings.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.prefs, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)
        except OSError as exc:
            self.notify('设置暂未保存：' + str(exc))

    def closeEvent(self, event):
        if self.closing:
            if all(not s.worker.isRunning() for s in self.sessions.values()) and not (self.scan and self.scan.isRunning()):
                event.accept()
                return
            event.ignore()
            return
        self._save_prefs()
        self.closing = True
        self.capture_timer.stop()
        for s in self.sessions.values():
            s.worker.shutdown()
        self.centralWidget().setEnabled(False)
        self.notify('正在完成照片和录像保存并释放摄像头，请稍候…')
        self.close_timer.start()
        event.ignore()

    def _finish_close(self):
        if all(not s.worker.isRunning() for s in self.sessions.values()) and not (self.scan and self.scan.isRunning()):
            self.close_timer.stop()
            self.close()
