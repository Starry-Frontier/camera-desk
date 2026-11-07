"""Opt-in, single-frame requests to external vision model APIs."""
from base64 import b64encode
from dataclasses import dataclass
from ipaddress import ip_address
import json
import re

from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Qt, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest, QSslSocket


PROVIDERS = {
    'openai': ('OpenAI 兼容', 'https://api.openai.com/v1'),
    'claude': ('Claude Messages', 'https://api.anthropic.com/v1'),
    'gemini': ('Gemini GenerateContent', 'https://generativelanguage.googleapis.com/v1beta'),
    'grok': ('Grok（xAI）', 'https://api.x.ai/v1'),
    'deepseek': ('DeepSeek', 'https://api.deepseek.com'),
    'qwen': ('Qwen（阿里云百炼）', 'https://dashscope.aliyuncs.com/compatible-mode/v1'),
    'glm': ('GLM（智谱）', 'https://open.bigmodel.cn/api/paas/v4'),
}
_MAX_RESPONSE_BYTES = 1024 * 1024
_TIMEOUT_MS = 60_000


def _loopback(host):
    if host.lower() == 'localhost':
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ModelConnection:
    provider: str = 'openai'
    base_url: str = PROVIDERS['openai'][1]
    model: str = ''

    @classmethod
    def from_preferences(cls, values):
        if isinstance(values, dict):
            fields = [values.get(name) for name in ('provider', 'base_url', 'model')]
            if all(isinstance(value, str) for value in fields):
                connection = cls(*fields)
                try:
                    connection.request_url()
                    return connection
                except ValueError:
                    pass
        return cls()

    def request_url(self):
        if self.provider not in PROVIDERS:
            raise ValueError('请选择受支持的接口协议。')
        model = self.model.strip()
        if not model or len(model) > 200 or not model.isprintable():
            raise ValueError('请输入有效的图像模型 ID（最多 200 个字符）。')
        url = QUrl(self.base_url.strip(), QUrl.StrictMode)
        if (not url.isValid() or not url.host() or url.userInfo() or url.hasQuery() or url.hasFragment()
                or url.scheme() not in ('https', 'http')):
            raise ValueError('请输入有效的 API 基础地址，地址中不要包含账号、密码、参数或片段。')
        if url.scheme() == 'http' and not _loopback(url.host()):
            raise ValueError('远程模型接口需要 HTTPS。本机回环地址可使用 HTTP。')
        if self.provider == 'claude':
            path = '/messages'
        elif self.provider == 'gemini':
            model = model[7:] if model.startswith('models/') else model
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', model):
                raise ValueError('Gemini 模型 ID 应为模型名称，可带 models/ 前缀。')
            path = '/models/' + model + ':generateContent'
        else:
            path = '/chat/completions'
        url.setPath(url.path(QUrl.FullyEncoded).rstrip('/') + path, QUrl.StrictMode)
        return url


def _request_body(connection, prompt, encoded_image):
    model = connection.model.strip()
    if connection.provider == 'claude':
        return {'model': model, 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': encoded_image}},
            {'type': 'text', 'text': prompt},
        ]}]}
    if connection.provider == 'gemini':
        return {'contents': [{'role': 'user', 'parts': [
            {'inlineData': {'mimeType': 'image/jpeg', 'data': encoded_image}}, {'text': prompt},
        ]}]}
    # The GLM vision API documents raw Base64 in image_url.url.
    image_url = encoded_image if connection.provider == 'glm' else 'data:image/jpeg;base64,' + encoded_image
    return {'model': model, 'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': prompt},
        {'type': 'image_url', 'image_url': {'url': image_url}},
    ]}]}


def _response_text(provider, data):
    try:
        payload = json.loads(data)
        if provider in ('claude', 'gemini'):
            if provider == 'claude':
                parts = payload['content']
            else:
                parts = payload['candidates'][0]['content']['parts']
            result = '\n'.join(part['text'] for part in parts
                               if isinstance(part, dict) and isinstance(part.get('text'), str)
                               and part.get('type', 'text') == 'text' and not part.get('thought'))
        else:
            message = payload['choices'][0]['message']
            result = message.get('content') or message.get('refusal')
        if isinstance(result, str) and result.strip():
            return result.strip()
    except (ValueError, TypeError, AttributeError, IndexError, KeyError, RecursionError):
        pass
    raise ValueError('模型没有返回可显示的文本。请检查模型是否支持图片输入及所选协议。')


class ModelClient(QObject):
    """The core gate and the sole transport for model requests."""

    enabled_changed = Signal(bool)
    busy_changed = Signal(bool)
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._network = QNetworkAccessManager(self)
        self._reply = None
        self._response = bytearray()
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(lambda: self.cancel('模型请求超时，请检查服务状态后重试。'))

    @property
    def enabled(self):
        return self._enabled

    @property
    def busy(self):
        return self._reply is not None

    def set_enabled(self, enabled):
        enabled = enabled is True
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.cancel('模型接口已关闭，已取消本地等待。')
        self.enabled_changed.emit(enabled)

    def analyze(self, connection, api_key, prompt, image):
        if not self.enabled:
            raise ValueError('模型接口已关闭。')
        if self.busy:
            raise ValueError('已有一个分析请求正在进行。')
        url = connection.request_url()
        key = api_key.strip()
        if key and not re.fullmatch(r'[!-~]{1,4096}', key):
            raise ValueError('API 密钥包含空白或无效字符。')
        if not key and connection.provider != 'openai':
            raise ValueError('此协议需要填写 API 密钥。')
        prompt = prompt.strip()
        if not prompt or len(prompt) > 4000:
            raise ValueError('请输入分析问题（最多 4000 个字符）。')
        if image is None or image.isNull():
            raise ValueError('当前选中机位没有可用画面。')
        if url.scheme() == 'https' and not QSslSocket.supportsSsl():
            raise ValueError('当前运行环境无法建立 HTTPS 连接。')
        if max(image.width(), image.height()) > 960:
            image = image.scaled(960, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, 'JPEG', 85):
            raise ValueError('当前画面无法编码为 JPEG。')
        encoded_image = b64encode(bytes(buffer.data())).decode('ascii')
        request = QNetworkRequest(url)
        request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
        request.setRawHeader(b'Accept', b'application/json')
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
        for attribute in (QNetworkRequest.CookieLoadControlAttribute, QNetworkRequest.CookieSaveControlAttribute,
                          QNetworkRequest.AuthenticationReuseAttribute):
            request.setAttribute(attribute, QNetworkRequest.Manual)
        if connection.provider == 'claude':
            request.setRawHeader(b'anthropic-version', b'2023-06-01')
            request.setRawHeader(b'x-api-key', key.encode('ascii'))
        elif connection.provider == 'gemini':
            request.setRawHeader(b'x-goog-api-key', key.encode('ascii'))
        elif key:
            request.setRawHeader(b'Authorization', b'Bearer ' + key.encode('ascii'))
        # Local endpoints must stay local even with a system proxy configured.
        proxy = QNetworkProxy.NoProxy if _loopback(url.host()) else QNetworkProxy.DefaultProxy
        self._network.setProxy(QNetworkProxy(proxy))
        body = json.dumps(_request_body(connection, prompt, encoded_image), ensure_ascii=False).encode('utf-8')
        reply = self._network.post(request, body)
        self._reply = reply
        reply.setReadBufferSize(64 * 1024)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply, connection.provider))
        self._timeout.start(_TIMEOUT_MS)
        self.busy_changed.emit(True)

    def _read(self, reply):
        if reply is not self._reply:
            return
        if len(self._response) + reply.bytesAvailable() > _MAX_RESPONSE_BYTES:
            self.cancel('模型返回内容超过大小限制。')
            return
        if reply.bytesAvailable():
            self._response.extend(bytes(reply.readAll()))

    def _release(self):
        reply = self._reply
        self._reply = None
        self._timeout.stop()
        self._response.clear()
        return reply

    def cancel(self, message='已取消本地等待。'):
        if not self.busy:
            return
        reply = self._release()
        reply.abort()
        reply.deleteLater()
        self.busy_changed.emit(False)
        self.failed.emit(message)

    def _finished(self, reply, provider):
        self._read(reply)
        if reply is not self._reply:
            return
        data = bytes(self._response)
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        network_error = reply.error() != QNetworkReply.NoError
        self._release()
        reply.deleteLater()
        self.busy_changed.emit(False)
        if isinstance(status, int) and 300 <= status < 400:
            self.failed.emit('模型接口要求重定向，请核对并填写实际 API 基础地址。')
        elif isinstance(status, int) and status >= 400:
            self.failed.emit('模型接口返回 HTTP %d。请核对地址、密钥、模型 ID 和服务权限。' % status)
        elif network_error or not isinstance(status, int) or not 200 <= status < 300:
            self.failed.emit('无法连接模型接口，请检查地址、网络或 HTTPS 证书。')
        else:
            try:
                result = _response_text(provider, data)
            except ValueError as exc:
                self.failed.emit(str(exc))
            else:
                self.succeeded.emit(result)
