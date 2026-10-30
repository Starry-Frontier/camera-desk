"""One dialog prepares all camera filenames before issuing batch commands."""
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                              QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .saving import SaveTarget, media_path, validate_filename, validate_targets
from .media_formats import change_suffix, format_for_extension, formats_for


class BatchSaveDialog(QDialog):
    def __init__(self, sessions, directory, kind, parent=None, default_format=None):
        super().__init__(parent)
        self.sessions, self.kind = list(sessions), kind
        self.targets = []
        self.edits, self.custom_names = {}, set()
        self.setWindowTitle('保存照片 · %d 个机位' % len(sessions) if kind == 'photo' else '设置录像文件 · %d 个机位' % len(sessions))
        self.setMinimumWidth(620)
        self.resize(720, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.directory = QLineEdit(str(directory))
        self.directory.setObjectName('saveDirectory')
        self.directory.setToolTip('只需选择一次，本次所有机位的照片或录像共用此文件夹。')
        browse = QPushButton('浏览…')
        browse.clicked.connect(self.browse)
        folder_row.addWidget(self.directory, 1)
        folder_row.addWidget(browse)
        form.addRow('统一保存文件夹', folder_row)
        self.format_combo = QComboBox()
        self.format_combo.setObjectName('batchMediaFormat')
        for spec in formats_for(kind):
            self.format_combo.addItem(spec.file_filter, spec.suffix)
        selected = format_for_extension(kind, default_format or ('.png' if kind == 'photo' else '.avi'))
        self.format_combo.setCurrentIndex(self.format_combo.findData(selected.suffix))
        form.addRow('图片格式' if kind == 'photo' else '视频格式', self.format_combo)
        self.prefix = QLineEdit(datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.prefix.setObjectName('savePrefix')
        self.prefix.setToolTip('统一名称后自动加机位编号；下面也可单独修改文件名。')
        form.addRow('统一名称', self.prefix)
        layout.addLayout(form)
        hint = QLabel('所有机位的文件均直接保存在同一文件夹内；按机位编号区分，也可分别修改文件名。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        names = QFormLayout(host)
        for index, s in enumerate(self.sessions, 1):
            edit = QLineEdit()
            edit.setObjectName('filename_' + s.key)
            edit.textEdited.connect(lambda _text, key=s.key: self.custom_names.add(key))
            self.edits[s.key] = edit
            number = getattr(s, 'number', index) or index
            title = QLabel('%02d · %s' % (number, s.device.name))
            title.setWordWrap(True)
            title.setMaximumWidth(220)
            names.addRow(title, edit)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.prefix.textChanged.connect(self.update_names)
        self.format_combo.currentIndexChanged.connect(self.update_format)
        self.update_names()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('保存照片' if kind == 'photo' else '开始录制')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, '选择保存目录', self.directory.text())
        if folder:
            self.directory.setText(folder)

    def update_names(self, *_):
        suffix = self.format_combo.currentData()
        for index, s in enumerate(self.sessions, 1):
            if s.key not in self.custom_names:
                number = getattr(s, 'number', index) or index
                self.edits[s.key].setText('%s_机位%02d%s' % (self.prefix.text(), number, suffix))

    def update_format(self, *_):
        for key in self.custom_names:
            if self.edits[key].text():
                self.edits[key].setText(str(change_suffix(self.edits[key].text(), self.kind, self.format_combo.currentData())))
        self.update_names()

    def build_targets(self):
        raw_folder = self.directory.text().strip()
        if not raw_folder:
            raise ValueError('请选择保存目录。')
        folder = Path(raw_folder).resolve()
        if folder.exists() and not folder.is_dir():
            raise ValueError('保存位置不是一个文件夹。')
        targets = []
        for s in self.sessions:
            name = self.edits[s.key].text()
            validate_filename(name)  # Reject paths/traversal before joining.
            targets.append(SaveTarget(s.key, media_path(folder / name, self.kind, self.format_combo.currentData())))
        return validate_targets(targets, shared_directory=folder)

    def accept(self):
        try:
            targets = self.build_targets()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '请检查保存设置', str(exc))
            return
        self.targets = targets
        super().accept()
