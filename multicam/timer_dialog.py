"""User-selected start, duration, interval and storage planning."""
import time
from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                              QVBoxLayout, QMessageBox)
from .widgets import SelectionComboBox, DateTimePicker, DurationEdit, PhotoIntervalEdit
from .storage import estimate_storage, format_bytes, free_space


class TimerDialog(QDialog):
    def __init__(self, sessions, selected, directory, formats, parent=None):
        super().__init__(parent)
        self.sessions, self.directory, self.formats = list(sessions), directory, formats
        self.plan = None
        self.setWindowTitle('定时拍摄与存储预估')
        self.setMinimumWidth(610)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.scope = SelectionComboBox()
        self.scope.addItem('全部已添加机位', None)
        for s in self.sessions:
            self.scope.addItem('%02d · %s' % (s.number, s.device.name), s.key)
        if selected is not None:
            self.scope.setCurrentIndex(self.scope.findData(selected))
        form.addRow('机位', self.scope)
        self.kind = SelectionComboBox()
        self.kind.addItem('录像（达到时长自动停止）', 'video')
        self.kind.addItem('间隔自动拍照', 'photo')
        form.addRow('拍摄方式', self.kind)
        self.start_mode = SelectionComboBox()
        self.start_mode.addItems(['立即开始', '预约开始时间'])
        form.addRow('开始方式', self.start_mode)
        self.start_at = DateTimePicker(QDateTime.currentDateTime().addSecs(60))
        form.addRow('本地开始日期 / 时刻', self.start_at)
        self.start_hint = QLabel()
        self.start_hint.setWordWrap(True)
        form.addRow('', self.start_hint)
        self.duration = DurationEdit(60)
        form.addRow('拍摄持续时间', self.duration)
        self.interval_editor = PhotoIntervalEdit()
        self.interval = self.interval_editor.spin
        form.addRow('拍照间隔', self.interval_editor)
        self.interval_hint = QLabel()
        self.interval_hint.setWordWrap(True)
        form.addRow('', self.interval_hint)
        layout.addLayout(form)
        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        self.estimate.setObjectName('storageEstimate')
        layout.addWidget(self.estimate)
        hint = QLabel('使用左侧预设目录和格式，自动命名且不覆盖已有文件。\n'
                      '预约需保持软件运行、电脑不休眠；错过开始时间超过 60 秒会取消。\n'
                      '照片从开始时拍第一张，忙碌或延迟时跳过错过的轮次。存储为粗略范围，画面复杂度会影响实际大小。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('创建拍摄任务')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for combo in (self.scope, self.kind, self.start_mode):
            combo.currentIndexChanged.connect(self.update_estimate)
        self.start_at.dateTimeChanged.connect(self.schedule_time_edited)
        for box in self.duration.parts:
            box.valueChanged.connect(self.update_estimate)
        self.interval.valueChanged.connect(self.photo_interval_edited)
        self.update_estimate()

    def chosen(self):
        key = self.scope.currentData()
        return [s for s in self.sessions if key is None or key == s.key]

    def schedule_time_edited(self, *_):
        self.start_mode.setCurrentIndex(1)
        self.update_estimate()

    def photo_interval_edited(self, *_):
        self.kind.setCurrentIndex(1)
        self.update_estimate()

    def update_estimate(self, *_):
        photo = self.kind.currentData() == 'photo'
        self.interval_hint.setText('按此间隔拍照；任务开始后仍可在主界面调整。' if photo
                                   else '修改拍照间隔将自动选择“间隔自动拍照”。')
        self.start_hint.setText('已选择预约：按所选日期和时、分、秒开始拍摄。' if self.start_mode.currentIndex() == 1
                                else '当前为立即开始；修改上方日期或时、分、秒，将自动切换为预约。')
        suffix = self.formats['photo' if photo else 'video']
        result = estimate_storage([s.config for s in self.chosen()], self.duration.value(), suffix,
                                  self.interval.value() if photo else None)
        available = free_space(self.directory)
        self.estimate.setText('格式：%s · %d 个机位%s\n预估占用：%s ～ %s\n磁盘剩余：%s\n保存目录：%s' % (
            suffix, len(self.chosen()), ' · 预计 %d 张照片' % result['photos'] if photo else '',
            format_bytes(result['minimum_bytes']), format_bytes(result['maximum_bytes']),
            format_bytes(available) if available is not None else '无法读取', self.directory))
        self.estimate.setStyleSheet('color: #a83e37;' if available is not None and result['maximum_bytes'] > available else '')

    def accept(self):
        self.interval.interpretText()
        duration = self.duration.value()
        starts_at = self.start_at.dateTime().toMSecsSinceEpoch() / 1000 if self.start_mode.currentIndex() else time.time()
        if duration <= 0 or not self.chosen() or (self.start_mode.currentIndex() and starts_at <= time.time()):
            QMessageBox.warning(self, '请检查拍摄设置', '请选择机位、正数拍摄时长，以及未来的预约时间。')
            return
        self.plan = {'keys': [s.key for s in self.chosen()], 'kind': self.kind.currentData(),
                     'starts_at': starts_at, 'duration': duration, 'interval': self.interval.value()}
        super().accept()


class PhotoIntervalDialog(QDialog):
    """Change existing photo tasks without recreating or extending them."""
    def __init__(self, sessions, selected=None, parent=None):
        super().__init__(parent)
        self.sessions = list(sessions)
        self.plan = None
        self.setWindowTitle('调整拍照间隔')
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.scope = SelectionComboBox()
        self.scope.addItem('全部拍照任务', None)
        for s in self.sessions:
            self.scope.addItem('%02d · %s' % (s.number, s.device.name), s.key)
        if selected is not None:
            self.scope.setCurrentIndex(self.scope.findData(selected))
        form.addRow('应用到', self.scope)
        self.interval_editor = PhotoIntervalEdit()
        self.interval = self.interval_editor.spin
        form.addRow('新的拍照间隔', self.interval_editor)
        layout.addLayout(form)
        self.current = QLabel()
        self.current.setWordWrap(True)
        layout.addWidget(self.current)
        hint = QLabel('运行中的任务从应用时刻重新计时，在新间隔后拍下一张；原定结束时间不变。\n'
                      '预约任务从原定开始时间使用新间隔。保存位置、图片格式和正在录制的视频保持原设置。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('应用间隔')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.scope.currentIndexChanged.connect(self.load_current)
        self.load_current()

    def chosen(self):
        key = self.scope.currentData()
        return [s for s in self.sessions if key is None or s.key == key]

    def load_current(self, *_):
        plans = [(s, s.photo_plan or (s.schedule if s.schedule and s.schedule['kind'] == 'photo' else None))
                 for s in self.chosen()]
        plans = [(s, p) for s, p in plans if p]
        if plans:
            self.interval.setValue(plans[0][1]['interval'])
        self.current.setText('当前间隔：\n' + '\n'.join('%02d · %g 秒 / 张（%s）' %
            (s.number, p['interval'], '进行中' if s.photo_plan is p else '已预约') for s, p in plans)
            if plans else '所选拍照任务已结束。')

    def accept(self):
        self.interval.interpretText()
        self.plan = {'keys': [s.key for s in self.chosen()], 'interval': self.interval.value()}
        super().accept()
