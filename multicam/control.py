"""Authenticated loopback control of the running Camera Desk window."""
from base64 import b64encode
from concurrent.futures import Future, TimeoutError
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import re
import secrets
from threading import BoundedSemaphore, Thread
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal, Slot

from .capture import CameraConfig


_CONTROL_IDLE_SECONDS = 120


class CommandError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class ControlServer(ThreadingHTTPServer):
    def __init__(self, dispatch):
        self.token = secrets.token_urlsafe(32)
        self.dispatch = dispatch
        self._slots = BoundedSemaphore(8)
        super().__init__(('127.0.0.1', 0), ControlHandler)

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()


class ControlHandler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *_):
        pass

    def do_POST(self):
        try:
            if self.headers.get('Host') != '127.0.0.1:%d' % self.server.server_port or self.headers.get('Origin') is not None:
                raise CommandError(403, '只接受本机工具的直接请求。')
            supplied = self.headers.get('Authorization', '').encode('latin1')
            expected = ('Bearer ' + self.server.token).encode('ascii')
            if not secrets.compare_digest(supplied, expected):
                raise CommandError(401, '本机控制凭据无效。')
            if self.path != '/v1/control':
                raise CommandError(404, '未知的控制接口。')
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise CommandError(400, '请提交带 Content-Length 的 JSON 请求。')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                raise CommandError(400, '控制请求大小须在 1 至 8192 字节内。')
            body = json.loads(self.rfile.read(size).decode('utf-8'))
            if not isinstance(body, dict) or not isinstance(body.get('action'), str):
                raise CommandError(400, '请求必须包含 action。')
            arguments = body.get('arguments', {})
            if not isinstance(arguments, dict):
                raise CommandError(400, 'arguments 必须是 JSON 对象。')
            pending = Future()
            controller = self.headers.get('X-CameraDesk-Controller', '')
            self.server.dispatch(self.server, body['action'], arguments, controller, pending)
            try:
                result = pending.result(timeout=4)
            except TimeoutError:
                if pending.cancel():
                    raise CommandError(503, '窗口未及时处理请求，操作已取消。')
                raise CommandError(504, '操作已交给窗口处理，请查询状态再决定是否重试。')
        except CommandError as exc:
            self._reply(exc.status, {'error': str(exc)})
        except (ValueError, UnicodeError, RecursionError, OSError):
            self._reply(400, {'error': '无法读取控制请求。'})
        else:
            self._reply(200, {'result': result})

    def _reply(self, status, body):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # The client may have closed its connection.


class LocalControl(QObject):
    _requested = Signal(object, str, object, str, object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._server = None
        self._session_file = None
        self._instance = secrets.token_urlsafe(32)
        self._controller = ''
        self._controller_deadline = 0.0
        self._pending_photos = {}
        self._finished_photos = {}
        self._requested.connect(self._execute, Qt.QueuedConnection)

    def start(self):
        if self._server is not None:
            return self._server.server_port
        server = ControlServer(self._requested.emit)
        path = self.window.data_dir / ('control-%d.json' % os.getpid())
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump({'url': 'http://127.0.0.1:%d/v1/control' % server.server_port,
                           'token': server.token, 'instance': self._instance}, stream)
            temporary.replace(path)
            Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True).start()
        except (OSError, RuntimeError):
            server.server_close()
            for file in (temporary, path):
                try:
                    file.unlink(missing_ok=True)
                except OSError:
                    logging.warning('Could not remove local control session file')
            raise
        self._server, self._session_file = server, path
        return server.server_port

    def stop(self):
        if self._server is None:
            return
        server, self._server = self._server, None
        self._controller = ''
        self._controller_deadline = 0.0
        server.shutdown()
        server.server_close()
        try:
            self._session_file.unlink(missing_ok=True)
        except OSError:
            logging.warning('Could not remove local control session file')
        self._session_file = None

    @Slot(object, str, object, str, object)
    def _execute(self, server, action, arguments, controller, pending):
        if not pending.set_running_or_notify_cancel():
            return
        if server is not self._server or self.window.closing:
            pending.set_exception(CommandError(503, '本机控制已关闭。'))
            return
        try:
            pending.set_result(self._authorized_command(action, arguments, controller))
        except CommandError as exc:
            pending.set_exception(exc)
        except (ValueError, OSError, TypeError, OverflowError) as exc:
            pending.set_exception(CommandError(400, str(exc)))
        except Exception:
            logging.exception('Local camera control command failed')
            pending.set_exception(CommandError(500, '控制操作失败，请查看软件日志。'))

    def _authorized_command(self, action, args, controller):
        # Only the Qt thread checks and changes ownership, serializing it with
        # command dispatch and with the user's local-control switch.
        now = monotonic()
        if now >= self._controller_deadline:
            self._controller = ''
        if action == 'control_info' and not args:
            return {'exclusive_control': True, 'occupied': bool(self._controller)}
        valid_controller = re.fullmatch(r'[A-Za-z0-9_-]{43}', controller)
        if self._controller and (not valid_controller or not secrets.compare_digest(self._controller, controller)):
            raise CommandError(409, '已有其他客户端持有 Camera Desk 的控制权。')
        if not valid_controller:
            raise CommandError(423, '请先申请 Camera Desk 的控制权。')
        if action == 'acquire_control' and not args:
            self._controller = controller
            self._controller_deadline = now + _CONTROL_IDLE_SECONDS
            return {'acquired': True, 'idle_timeout_seconds': _CONTROL_IDLE_SECONDS}
        if not self._controller:
            raise CommandError(423, '控制权已失效，请重新申请。')
        if action == 'release_control' and not args:
            self._controller = ''
            self._controller_deadline = 0.0
            return {'released': True}
        result = self._command(action, args)
        self._controller_deadline = monotonic() + _CONTROL_IDLE_SECONDS
        return result

    @Slot(str, bool, str)
    def snapshot_finished(self, request_id, saved, message):
        operation = self._pending_photos.pop(request_id, None)
        if operation is None:
            return
        operation.update(state='saved' if saved else 'failed', error=None if saved else message)
        self._finished_photos[request_id] = operation
        if len(self._finished_photos) > 256:
            del self._finished_photos[next(iter(self._finished_photos))]

    def _command(self, action, args):
        w = self.window
        fields = {
            'status': (), 'refresh_devices': (), 'add_camera': ('device_id',),
            'select_camera': ('camera_id',), 'preview': ('camera_id',),
            'snapshot': ('camera_id',), 'photo_status': ('request_id',),
            'start_recording': ('camera_id', 'duration_seconds'),
            'stop_recording': ('camera_id',),
            'set_format': ('camera_id', 'width', 'height', 'fps', 'native_only'),
        }
        if action not in fields or set(args) - set(fields[action]):
            raise CommandError(400, '未知的控制操作或参数。')
        if action == 'status':
            devices = [w.device_combo.itemData(i) for i in range(w.device_combo.count())]
            return {'selected': w.selected, 'scanning': bool(w.scan and w.scan.isRunning()),
                    'devices': [asdict(d) for d in devices],
                    'cameras': [{'id': s.key, 'name': s.device.name, 'ready': w._can_save(s),
                                 'recording': s.recording, 'record_action': s.record_action,
                                 'video_path': s.pending_save_path, 'last_video': s.last_video,
                                 'last_photo': s.last_photo, 'last_error': s.last_error or None,
                                 'format': asdict(s.config), 'format_pending': s.format_pending,
                                 'preview_ready': w._can_save(s) and not s.tile.view.image.isNull()}
                                for s in w.sessions.values()]}
        if action == 'refresh_devices':
            w.refresh_devices()
            return {'state': 'submitted'}
        if action == 'photo_status':
            request_id = args.get('request_id')
            if not isinstance(request_id, str) or not request_id:
                raise CommandError(400, '请提供拍照命令返回的 request_id。')
            operation = self._pending_photos.get(request_id) or self._finished_photos.get(request_id)
            if operation is None:
                raise CommandError(404, '拍照请求不存在或已超出保留范围。')
            return dict(operation)
        if action == 'add_camera':
            for i in range(w.device_combo.count()):
                device = w.device_combo.itemData(i)
                if device.device_id == args.get('device_id'):
                    w.add_camera(device)
                    return {'state': 'submitted', 'camera_id': device.device_id}
            raise CommandError(404, '设备未找到，请先刷新设备列表。')
        key = args.get('camera_id', w.selected)
        if not isinstance(key, str) or key not in w.sessions:
            raise CommandError(404, '没有此机位，请先查询状态。')
        s = w.sessions[key]
        if action == 'select_camera':
            if 'camera_id' not in args:
                raise CommandError(400, '请选择要选中的机位 ID。')
            w.select_session(key)
            return {'selected': key}
        if action == 'stop_recording':
            if not w._stop_record_one(s):
                raise CommandError(409, '此机位没有可停止的录像。')
            return {'state': 'submitted', 'camera_id': key, 'path': s.pending_save_path}
        if not w._can_save(s):
            raise CommandError(409, '该机位尚未就绪或正在切换格式。')
        if action == 'preview':
            image = s.tile.view.image
            if image.isNull():
                raise CommandError(409, '正在等待当前采集格式的画面。')
            if max(image.width(), image.height()) > 960:
                image = image.scaled(960, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, 'JPEG', 85):
                raise CommandError(500, '无法编码预览画面。')
            return {'camera_id': key, 'width': image.width(), 'height': image.height(),
                    'mime_type': 'image/jpeg', 'image_base64': b64encode(bytes(buffer.data())).decode('ascii')}
        if action == 'set_format':
            size = (args.get('width'), args.get('height'))
            fps = args.get('fps', s.config.fps)
            native = args.get('native_only', s.config.native_only)
            if any(type(v) is not int for v in size) or type(fps) not in (float, int) or type(native) is not bool:
                raise CommandError(400, '宽高须为整数，帧率须为数值，native_only 须为布尔值。')
            if not w._request_format(s, CameraConfig(*size, fps, native).validated()):
                raise CommandError(409, '此机位暂时无法切换采集格式。')
            return {'state': 'submitted', 'camera_id': key}
        if action == 'snapshot':
            target, = w._automatic_targets([s], 'photo')
            request_id = uuid4().hex
            if not s.worker.take_snapshot(target.path, request_id=request_id):
                raise CommandError(409, '机位暂时无法接受拍照请求。')
            operation = {'request_id': request_id, 'state': 'submitted', 'camera_id': key,
                         'path': str(target.path), 'error': None}
            self._pending_photos[request_id] = operation
            return dict(operation)
        duration = args.get('duration_seconds', 0)
        if type(duration) not in (int, float) or not 0 <= duration <= 604800:
            raise CommandError(400, '录像时长须为 0（不限）至 604800 秒。')
        if not w._can_start_recording(s):
            raise CommandError(409, '该机位正在录像、保存或已有录像预约。')
        target, = w._automatic_targets([s], 'video')
        w._start_record_one(s, target, duration_seconds=duration)
        return {'state': 'submitted', 'camera_id': key, 'path': str(target.path)}
