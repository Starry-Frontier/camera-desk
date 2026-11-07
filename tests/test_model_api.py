"""Exercise the real asynchronous transport against a loopback test server."""
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from queue import Queue
import threading

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QPushButton

from multicam.model_api import ModelClient, ModelConnection
from test_audit_regressions import AuditWindow, app, wait_for, window


@pytest.fixture
def api_server():
    requests = Queue()
    responses = Queue()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.put((self.path, {name.lower(): value for name, value in self.headers.items()}, body))
            code, headers, payload, delayed = responses.get(timeout=3)
            if delayed:
                release.wait(3)
            response = json.dumps(payload).encode('utf-8')
            try:
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(response)
            except OSError:
                pass  # A disabled interface aborts the client connection.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d/v1' % server.server_port, requests, responses, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def sample_frame():
    frame = QImage(20, 1500, QImage.Format_RGB32)
    frame.fill(QColor('darkBlue'))
    return frame


CHAT_RESULT = {'choices': [{'message': {'content': '结果'}}]}


@pytest.mark.parametrize('provider, route, auth, result', [
    ('openai', '/chat/completions', 'authorization', CHAT_RESULT),
    ('grok', '/chat/completions', 'authorization', CHAT_RESULT),
    ('deepseek', '/chat/completions', 'authorization', CHAT_RESULT),
    ('qwen', '/chat/completions', 'authorization', CHAT_RESULT),
    ('glm', '/chat/completions', 'authorization', CHAT_RESULT),
    ('claude', '/messages', 'x-api-key', {'content': [{'type': 'text', 'text': '结果'}]}),
    ('gemini', '/models/vision-test:generateContent', 'x-goog-api-key',
     {'candidates': [{'content': {'parts': [{'thought': True, 'text': '隐藏'}, {'text': '结果'}]}}]}),
])
def test_model_protocols_send_a_single_decodable_frame(app, api_server, provider, route, auth, result):
    base, requests, responses, _ = api_server
    responses.put((200, {}, result, False))
    client = ModelClient()
    client.set_enabled(True)
    results, errors = [], []
    client.succeeded.connect(results.append)
    client.failed.connect(errors.append)
    client.analyze(ModelConnection(provider, base, 'vision-test'), 'test-key', '画面是什么？', sample_frame())
    wait_for(app, lambda: bool(results or errors))
    assert results == ['结果'] and errors == [] and not client.busy
    path, headers, body = requests.get_nowait()
    assert path == '/v1' + route and requests.empty()
    assert headers[auth] == ('Bearer test-key' if auth == 'authorization' else 'test-key')
    assert 'test-key' not in json.dumps(body)
    if provider == 'claude':
        assert headers['anthropic-version'] == '2023-06-01'
        picture, prompt = body['messages'][0]['content']
        encoded = picture['source']['data']
    elif provider == 'gemini':
        picture, prompt = body['contents'][0]['parts']
        encoded = picture['inlineData']['data']
    else:
        assert body['model'] == 'vision-test'
        prompt, picture = body['messages'][0]['content']
        url = picture['image_url']['url']
        if provider == 'glm':
            encoded = url
        else:
            prefix, encoded = url.split(',', 1)
            assert prefix == 'data:image/jpeg;base64'
    assert prompt['text'] == '画面是什么？'
    image = QImage.fromData(b64decode(encoded, validate=True), 'JPEG')
    assert not image.isNull() and image.height() == 960


def test_new_provider_presets_use_documented_addresses_and_clear_previous_key(window):
    window.show_model_dialog()
    dialog = window.model_dialog
    for provider, base, model in (
        ('grok', 'https://api.x.ai/v1', 'grok-4.7'),
        ('deepseek', 'https://api.deepseek.com', 'deepseek-flash'),
        ('qwen', 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3-vl-plus'),
        ('glm', 'https://open.bigmodel.cn/api/paas/v4', 'glm-4.6v'),
    ):
        dialog.api_key.setText('previous-service-key')
        index = dialog.provider.findData(provider)
        assert index >= 0
        dialog.provider.setCurrentIndex(index)
        assert dialog.base_url.text() == base and not dialog.api_key.text()
        dialog.model.setText(model)
        dialog.save_settings.click()
        saved = ModelConnection.from_preferences(window.prefs['model_api'])
        assert saved == ModelConnection(provider, base, model)
        assert saved.request_url().toString() == base + '/chat/completions'
        assert not window.model_client.enabled


def test_core_switch_blocks_direct_calls_and_aborts_pending_reply(app, api_server):
    base, requests, responses, release = api_server
    connection = ModelConnection('openai', base, 'vision-test')
    payload = {'choices': [{'message': {'content': '新结果'}}]}
    responses.put((200, {}, {'choices': [{'message': {'content': '已取消的旧结果'}}]}, True))
    responses.put((200, {}, payload, False))
    client = ModelClient()
    results, errors = [], []
    client.succeeded.connect(results.append)
    client.failed.connect(errors.append)
    with pytest.raises(ValueError, match='已关闭'):
        client.analyze(connection, '', '问题', sample_frame())
    assert requests.empty()
    client.set_enabled(True)
    client.analyze(connection, '', '问题', sample_frame())
    wait_for(app, lambda: not requests.empty())
    requests.get_nowait()
    with pytest.raises(ValueError, match='正在进行'):
        client.analyze(connection, '', '问题', sample_frame())
    client.set_enabled(False)
    assert not client.busy and errors == ['模型接口已关闭，已取消本地等待。']
    with pytest.raises(ValueError, match='已关闭'):
        client.analyze(connection, '', '问题', sample_frame())
    release.set()
    client.set_enabled(True)
    client.analyze(connection, '', '问题', sample_frame())
    wait_for(app, lambda: bool(results))
    assert results == ['新结果'] and requests.qsize() == 1


def test_request_does_not_follow_redirects(app, api_server):
    base, requests, responses, _ = api_server
    responses.put((307, {'Location': base + '/different-destination'}, {}, False))
    responses.put((200, {}, {'choices': [{'message': {'content': '不应被访问'}}]}, False))
    client = ModelClient()
    errors = []
    client.failed.connect(errors.append)
    client.set_enabled(True)
    client.analyze(ModelConnection('openai', base, 'vision-test'), 'test-key', '问题', sample_frame())
    wait_for(app, lambda: bool(errors))
    assert '重定向' in errors[0]
    assert requests.qsize() == 1


def test_remote_plain_http_and_credentials_in_url_are_rejected():
    for base in ('http://192.0.2.1/v1', 'https://user:secret@example.test/v1',
                 'https://example.test/v1?key=secret'):
        with pytest.raises(ValueError):
            ModelConnection('openai', base, 'vision-test').request_url()


def test_model_dialog_uses_selected_camera_and_remembers_only_connection(app, window, api_server):
    base, requests, responses, release = api_server
    window.prefs['cameras']['demo:1'] = {'config': {'width': 640, 'height': 360, 'fps': 20}}
    window.add_demo_cameras(2)
    wait_for(app, lambda: all(s.ready for s in window.sessions.values()))
    window.select_session('demo:1')
    button = next(button for button in window.findChildren(QPushButton) if button.text() == '模型分析…')
    button.click()
    dialog = window.model_dialog
    assert dialog.isVisible() and not dialog.enabled.isChecked() and not dialog.send.isEnabled()
    assert '02' in dialog.source.text() and requests.empty()
    dialog.base_url.setText(base)
    dialog.model.setText('vision-test')
    dialog.api_key.setText('test-key')
    dialog.save_settings.click()
    dialog.enabled.click()
    assert requests.empty() and dialog.send.isEnabled()
    responses.put((200, {}, {'choices': [{'message': {'content': '<b>模拟结果</b>'}}]}, False))
    dialog.send.click()
    window.select_session('demo:0')
    wait_for(app, lambda: bool(dialog.result.toPlainText()))
    assert dialog.result.toPlainText() == '<b>模拟结果</b>'
    assert '02' in dialog.result_source.text() and '01' in dialog.source.text()
    dialog.enabled.click()
    assert '02' in dialog.result_source.text() and dialog.result_source.isVisible()
    assert dialog.result.toPlainText() == '<b>模拟结果</b>'
    dialog.enabled.click()
    _, _, body = requests.get_nowait()
    data = body['messages'][0]['content'][1]['image_url']['url'].split(',', 1)[1]
    assert QImage.fromData(b64decode(data), 'JPEG').width() == 640
    saved = (window.data_dir / 'settings.json').read_text(encoding='utf-8')
    assert 'test-key' not in saved and dialog.prompt.toPlainText() not in saved
    assert json.loads(saved)['model_api'] == {
        'provider': 'openai', 'base_url': base, 'model': 'vision-test', 'enabled': True,
    }
    dialog.base_url.setText(base + '/other')
    assert not dialog.api_key.text()
    dialog.base_url.setText(base)
    responses.put((200, {}, {'choices': [{'message': {'content': '已取消'}}]}, True))
    dialog.send.click()
    wait_for(app, lambda: not requests.empty())
    dialog.enabled.click()
    assert not window.model_client.enabled and not window.model_client.busy and not dialog.send.isEnabled()
    release.set()
    restored = AuditWindow(window.data_dir)
    try:
        assert not restored.model_client.enabled
        assert restored.model_connection == ModelConnection('openai', base, 'vision-test')
    finally:
        restored.close()
        wait_for(app, lambda: not restored.close_timer.isActive())
