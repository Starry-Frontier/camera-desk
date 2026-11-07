"""Manual model analysis of the selected camera preview."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .model_api import ModelConnection, PROVIDERS
from .widgets import SelectionComboBox


class ModelDialog(QDialog):
    preferences_changed = Signal()

    def __init__(self, client, connection, current_frame, remember_connection, parent=None):
        super().__init__(parent)
        self.client = client
        self.current_frame = current_frame
        self.remember_connection = remember_connection
        self._frame_available = False
        self._request_source = ''
        self.setWindowTitle('模型分析')
        self.resize(640, 590)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox('启用模型 API')
        self.enabled.setChecked(client.enabled)
        layout.addWidget(self.enabled)
        hint = QLabel('点击发送时，软件会将当前选中机位的一帧预览图像和分析问题发给所填地址。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        self.provider = SelectionComboBox()
        for key, (label, _) in PROVIDERS.items():
            self.provider.addItem(label, key)
        self.provider.setCurrentIndex(self.provider.findData(connection.provider))
        self.base_url = QLineEdit(connection.base_url)
        self.base_url.setToolTip('填写 API 基础地址，保留版本路径，例如 /v1。软件会补全请求路径。')
        self.model = QLineEdit(connection.model)
        self.model.setMaxLength(200)
        self.model.setPlaceholderText('填写服务商提供的图像模型 ID')
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setMaxLength(4096)
        self.api_key.setPlaceholderText('仅保留在本次运行中')
        self.api_key.setToolTip('切换协议或修改地址会清空密钥。')
        form.addRow('接口协议', self.provider)
        form.addRow('API 基础地址', self.base_url)
        form.addRow('模型 ID', self.model)
        form.addRow('API 密钥', self.api_key)
        layout.addLayout(form)
        self.source = QLabel('请先在主窗口选择有预览画面的机位。')
        self.source.setTextFormat(Qt.PlainText)
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.prompt = QPlainTextEdit('请用中文描述这张画面，列出可见的主要物体。')
        self.prompt.setAccessibleName('分析问题')
        self.prompt.setFixedHeight(68)
        layout.addWidget(QLabel('分析问题'))
        layout.addWidget(self.prompt)
        self.result_source = QLabel()
        self.result_source.setTextFormat(Qt.PlainText)
        self.result_source.setWordWrap(True)
        self.result_source.hide()
        layout.addWidget(self.result_source)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setAccessibleName('模型分析结果')
        self.result.setPlaceholderText('模型返回的文本将在这里显示。')
        layout.addWidget(self.result, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.save_settings = QPushButton('保存连接设置')
        self.cancel_request = QPushButton('取消等待')
        self.cancel_request.setToolTip('取消本地等待。服务端可能已经收到请求。')
        self.send = QPushButton('发送当前画面并分析')
        self.send.setObjectName('primary')
        close = QPushButton('关闭')
        for button in (self.save_settings, self.cancel_request, self.send, close):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.enabled.clicked.connect(self._toggle_enabled)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.base_url.textChanged.connect(self.api_key.clear)
        self.save_settings.clicked.connect(self._save_connection)
        self.cancel_request.clicked.connect(lambda: client.cancel())
        self.send.clicked.connect(self._send)
        close.clicked.connect(self.reject)
        self.finished.connect(lambda _: client.cancel())
        client.enabled_changed.connect(self._enabled_changed)
        client.busy_changed.connect(self._update_controls)
        client.succeeded.connect(self._show_result)
        client.failed.connect(self.status.setText)
        self._enabled_changed(client.enabled)

    def set_camera(self, name):
        self._frame_available = bool(name)
        self.source.setText('当前机位：' + name if name else '请先在主窗口选择有预览画面的机位。')
        self._update_controls()

    def _connection(self):
        return ModelConnection(self.provider.currentData(), self.base_url.text().strip(), self.model.text().strip())

    def _provider_changed(self):
        self.base_url.setText(PROVIDERS[self.provider.currentData()][1])
        self.api_key.clear()
        self.model.clear()

    def _toggle_enabled(self, checked):
        self.client.set_enabled(checked)
        self.preferences_changed.emit()

    def _enabled_changed(self, enabled):
        self.enabled.setChecked(enabled)
        self._update_controls()
        self.status.setText('模型接口已开启。' if enabled else '模型接口已关闭。')

    def _update_controls(self):
        busy = self.client.busy
        for field in (self.provider, self.base_url, self.model, self.api_key, self.prompt, self.save_settings):
            field.setEnabled(not busy)
        self.send.setEnabled(self.client.enabled and self._frame_available and not busy)
        self.cancel_request.setEnabled(busy)

    def _save_connection(self):
        connection = self._connection()
        try:
            connection.request_url()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        saved = self.remember_connection(connection)
        self.status.setText('连接设置已保存。' if saved else '连接设置已应用，但无法写入设置文件。')

    def _send(self):
        name, frame = self.current_frame()
        connection = self._connection()
        try:
            self.client.analyze(connection, self.api_key.text(), self.prompt.toPlainText(), frame)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._request_source = name
        self.result_source.hide()
        self.result.clear()
        self.status.setText('正在分析 %s 的画面…' % name)

    def _show_result(self, text):
        self.result_source.setText('结果来源：' + self._request_source)
        self.result_source.show()
        self.result.setPlainText(text)
        self.status.setText('画面分析已完成。')
