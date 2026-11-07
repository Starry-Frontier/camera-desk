"""Exercise bundled model UI/transport against a local simulated service only."""
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QSslSocket

from .model_api import PROVIDERS, ModelConnection


def check_bundled_models(window, evidence, finished):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                provider = self.path.split('/')[1]
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if provider == 'claude':
                    image = body['messages'][0]['content'][0]['source']['data']
                    reply = {'content': [{'type': 'text', 'text': 'packaged-model-ok'}]}
                    assert self.path.endswith('/messages') and self.headers['x-api-key'] == 'packaged-test-key'
                elif provider == 'gemini':
                    image = body['contents'][0]['parts'][0]['inlineData']['data']
                    reply = {'candidates': [{'content': {'parts': [{'text': 'packaged-model-ok'}]}}]}
                    assert self.path.endswith('/models/vision-test:generateContent')
                    assert self.headers['x-goog-api-key'] == 'packaged-test-key'
                else:
                    image = body['messages'][0]['content'][1]['image_url']['url']
                    if provider != 'glm':
                        assert image.startswith('data:image/jpeg;base64,')
                        image = image.split(',', 1)[1]
                    reply = {'choices': [{'message': {'content': 'packaged-model-ok'}}]}
                    assert self.path.endswith('/chat/completions')
                    assert self.headers['Authorization'] == 'Bearer packaged-test-key'
                assert b64decode(image, validate=True).startswith(b'\xff\xd8')
                requests.append(provider)
                data = json.dumps(reply).encode('utf-8')
                self.send_response(200)
            except Exception:
                data = b'{"error":"invalid packaged model request"}'
                self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True).start()
    client = window.model_client
    evidence['model_api_default_disabled'] = not client.enabled
    evidence['model_https_available'] = QSslSocket.supportsSsl()
    window.select_session(next(iter(window.sessions)))
    window.show_model_dialog()
    dialog = window.model_dialog
    source = window._model_frame()[0]
    providers = iter(PROVIDERS)
    done = False

    def finish(error=None):
        nonlocal done
        if done:
            return
        done = True
        client.succeeded.disconnect(received)
        client.failed.disconnect(failed)
        client.set_enabled(False)
        dialog.close()
        server.shutdown()
        server.server_close()
        evidence['model_providers_verified'] = requests
        evidence['model_api_packaged_passed'] = error is None and requests == list(PROVIDERS)
        if error:
            evidence['errors'].append({'channel': 'models', 'message': error})
        finished()

    def failed(message):
        finish('Packaged model request failed: ' + message)

    def received(text):
        if text != 'packaged-model-ok' or dialog.result_source.text() != '结果来源：' + source:
            finish('Packaged model result/source mismatch')
        else:
            QTimer.singleShot(0, send_next)

    def send_next():
        if done:
            return
        provider = next(providers, None)
        if provider is None:
            client.set_enabled(False)
            try:
                client.analyze(ModelConnection(), '', 'test', window._model_frame()[1])
            except ValueError:
                evidence['model_api_disabled_gate'] = not client.busy
            finish()
            return
        dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
        dialog.base_url.setText('http://127.0.0.1:%d/%s/v1' % (server.server_port, provider))
        dialog.model.setText('vision-test')
        dialog.api_key.setText('packaged-test-key')
        dialog.send.click()
        if not client.busy:
            finish('Packaged model request did not start: ' + dialog.status.text())

    client.succeeded.connect(received)
    client.failed.connect(failed)
    dialog.enabled.click()
    QTimer.singleShot(0, send_next)
    QTimer.singleShot(15000, lambda: finish('Packaged model checks timed out') if not done else None)
