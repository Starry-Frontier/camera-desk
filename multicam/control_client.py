"""Command-line and MCP clients for an active Camera Desk control session."""
import argparse
from base64 import b64decode
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re
import secrets
import sys

from . import __version__


class ControlError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _schema(description, properties=None, required=()):
    return (description, {'type': 'object', 'properties': properties or {},
                          'required': list(required), 'additionalProperties': False})


_CAMERA = {'camera_id': {'type': 'string', 'description': '机位 ID，省略时使用选中机位。'}}
TOOLS = {
    'status': _schema('列出可添加设备、当前机位、采集格式、录像状态和最近保存路径。'),
    'refresh_devices': _schema('发起摄像头扫描。随后可查询 status 获取结果。'),
    'add_camera': _schema('根据设备 ID 添加摄像头。', {'device_id': {'type': 'string'}}, ('device_id',)),
    'select_camera': _schema('在 Camera Desk 中选中指定机位。', _CAMERA, ('camera_id',)),
    'preview': _schema('获取当前预览的单帧 JPEG 图像。', _CAMERA),
    'snapshot': _schema('拍照并返回请求 ID 和预期保存路径。用 photo_status 查询结果。', _CAMERA),
    'photo_status': _schema('按拍照请求 ID 查询保存进度、路径和错误。',
                            {'request_id': {'type': 'string'}}, ('request_id',)),
    'start_recording': _schema('开始录像，可指定自动停止时长。返回预期路径。', dict(
        _CAMERA, duration_seconds={'type': 'number', 'minimum': 0, 'maximum': 604800,
                                   'description': '秒数，省略或填 0 时持续录像直到手动停止。'})),
    'stop_recording': _schema('停止指定机位的录像，稍后查询 status 确认保存结果。', _CAMERA),
    'set_format': _schema('设置机位的采集尺寸。切换后需等待新预览。', dict(
        _CAMERA, width={'type': 'integer', 'minimum': 16, 'maximum': 7680},
        height={'type': 'integer', 'minimum': 16, 'maximum': 4320},
        fps={'type': 'number', 'minimum': 1, 'maximum': 120}, native_only={'type': 'boolean'}),
        ('width', 'height')),
}


def _descriptor(path):
    try:
        session = json.loads(path.read_text(encoding='utf-8'))
        match = re.fullmatch(r'http://127\.0\.0\.1:(\d{1,5})/v1/control', session['url'])
        port = int(match[1]) if match else 0
        token, instance = session['token'], session['instance']
        if not 0 < port <= 65535 or any(not re.fullmatch(r'[A-Za-z0-9_-]{43}', value) for value in (token, instance)):
            raise ValueError('invalid session')
    except (OSError, ValueError, KeyError, TypeError):
        raise ControlError('无法读取本机连接凭据，请检查 Camera Desk 的控制开关。') from None
    return port, token, instance


def _send(path, action, arguments, controller=None, *, instance=None):
    port, token, current_instance = _descriptor(path)
    if instance is not None and current_instance != instance:
        raise ControlError('原 Camera Desk 实例已退出。请建立新连接并确认目标机位。')
    connection = HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        body = json.dumps({'action': action, 'arguments': arguments}, ensure_ascii=False, allow_nan=False)
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        if controller is not None:
            headers['X-CameraDesk-Controller'] = controller
        connection.request('POST', '/v1/control', body.encode('utf-8'), headers)
        response = connection.getresponse()
        payload = json.loads(response.read(2 * 1024 * 1024))
        if response.status != 200:
            raise ControlError('HTTP %d %s' % (response.status, payload['error']), response.status)
        return payload['result']
    except (OSError, HTTPException, ValueError, TypeError, KeyError):
        raise ControlError('未收到有效响应。请检查软件与控制开关。提交过的操作请先查询状态。') from None
    finally:
        connection.close()


def _session_file(data_dir, pid):
    if pid is None:
        paths = list(data_dir.glob('control-*.json'))
        if not paths:
            raise ControlError('找不到本机控制会话，请在 Camera Desk 开启本机控制并核对数据目录。')
        if len(paths) > 1:
            active = []
            for path in paths:
                try:
                    _send(path, 'control_info', {})
                except ControlError:
                    continue
                active.append(path)
            if not active:
                raise ControlError('找不到可连接的 Camera Desk 实例，请核对数据目录和控制开关。')
            if len(active) > 1:
                raise ControlError('多个 Camera Desk 实例已开启控制，请用 --pid 指定进程。')
            paths = active
        return paths[0]
    return data_dir / ('control-%d.json' % pid)


class ControlSession:
    """Hold a single identity across calls without sharing it with other clients."""

    def __init__(self, data_dir, pid=None):
        self.data_dir, self.pid = data_dir, pid
        self._path = None
        self._instance = None
        self._holding = False
        self._controller = secrets.token_urlsafe(32)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()

    def call(self, action, arguments):
        path, instance = self._path, self._instance
        if path is None:
            path = _session_file(self.data_dir, self.pid)
            _, _, instance = _descriptor(path)
        if self._holding:
            try:
                return _send(path, action, arguments, self._controller, instance=instance)
            except ControlError as exc:
                if exc.status != 423:
                    raise
                # HTTP 423 means this request was rejected before dispatch.
                self._holding = False
        try:
            _send(path, 'acquire_control', {}, self._controller, instance=instance)
        except ControlError as exc:
            if exc.status is not None:
                self._path, self._instance = path, instance
            self._holding = self._path is not None and exc.status in (None, 504)
            raise
        self._path, self._instance = path, instance
        self._holding = True
        return _send(path, action, arguments, self._controller, instance=instance)

    def release(self):
        if not self._holding:
            return False
        try:
            _send(self._path, 'release_control', {}, self._controller, instance=self._instance)
        except ControlError as exc:
            if exc.status not in (409, 423):
                # The request may not have run. Retain the identity so the
                # client can retry release or continue using its own lease.
                return False
            released = False
        else:
            released = True
        self._holding = False
        self._controller = secrets.token_urlsafe(32)
        return released


def call(data_dir, pid, action, arguments):
    with ControlSession(data_dir, pid) as session:
        return session.call(action, arguments)


def _mcp_result(session, method, params):
    if method == 'ping':
        return {}
    if method == 'tools/list':
        tools = dict(TOOLS, release_control=_schema('放弃当前 MCP 会话的独占控制权，允许其他客户端接管。'))
        return {'tools': [{'name': 'camera_desk_' + name, 'description': description, 'inputSchema': schema}
                          for name, (description, schema) in tools.items()]}
    if method != 'tools/call':
        raise LookupError
    name = params.get('name')
    arguments = params.get('arguments', {})
    prefix = 'camera_desk_'
    if not isinstance(name, str) or not name.startswith(prefix):
        raise ValueError('未知工具。')
    if not isinstance(arguments, dict):
        raise ValueError('arguments 必须是对象。')
    action = name[len(prefix):]
    if action not in TOOLS and action != 'release_control':
        raise ValueError('未知工具。')
    try:
        if action == 'release_control':
            if arguments:
                raise ValueError('释放控制权不接受参数。')
            result = {'released': session.release()}
        else:
            result = session.call(action, arguments)
    except ControlError as exc:
        return {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
    content = []
    if action == 'preview':
        result = result.copy()
        content.append({'type': 'image', 'mimeType': result.pop('mime_type'), 'data': result.pop('image_base64')})
    content.append({'type': 'text', 'text': json.dumps(result, ensure_ascii=False)})
    return {'content': content}


def run_mcp(session):
    versions = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
    negotiated = ready = False
    seen_ids = set()
    for line in sys.stdin.buffer:
        response = {'jsonrpc': '2.0', 'id': None}
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            response['error'] = {'code': -32700, 'message': 'Invalid JSON'}
        else:
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
                response['error'] = {'code': -32600, 'message': 'Invalid request'}
            elif 'id' not in message:
                if message['method'] == 'notifications/initialized' and negotiated:
                    ready = True
                continue
            elif type(message['id']) not in (str, int):
                response['error'] = {'code': -32600, 'message': 'Request ID must be a string or integer'}
            elif message['id'] in seen_ids:
                response['id'] = message['id']
                response['error'] = {'code': -32600, 'message': 'Duplicate request ID'}
            else:
                response['id'] = message['id']
                seen_ids.add(message['id'])
                method, params = message['method'], message.get('params', {})
                try:
                    if not isinstance(params, dict):
                        raise ValueError('params must be an object')
                    if method == 'initialize':
                        version = params.get('protocolVersion')
                        if not isinstance(version, str):
                            raise ValueError('protocolVersion is required')
                        negotiated = True
                        response['result'] = {'protocolVersion': version if version in versions else versions[0],
                                              'serverInfo': {'name': 'camera-desk', 'version': __version__},
                                              'capabilities': {'tools': {}}}
                    elif ready or method == 'ping':
                        response['result'] = _mcp_result(session, method, params)
                    else:
                        response['error'] = {'code': -32002, 'message': 'Initialize the MCP session first'}
                except LookupError:
                    response['error'] = {'code': -32601, 'message': 'Method not found'}
                except ValueError as exc:
                    response['error'] = {'code': -32602, 'message': str(exc)}
        sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
        sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description='通过本机接口控制正在运行的 Camera Desk')
    parser.add_argument('action', choices=(*TOOLS, 'mcp'))
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    parser.add_argument('--pid', type=int, help='同时运行多个实例时指定进程 ID')
    parser.add_argument('--camera', dest='camera_id')
    parser.add_argument('--device', dest='device_id')
    parser.add_argument('--request-id')
    parser.add_argument('--duration', dest='duration_seconds', type=float)
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--fps', type=float)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--native-only', dest='native_only', action='store_true', default=None)
    mode.add_argument('--allow-scaling', dest='native_only', action='store_false', default=None)
    parser.add_argument('--out', type=Path, help='将 preview 的 JPEG 写入指定的新文件')
    options = parser.parse_args()
    if options.action == 'mcp':
        with ControlSession(options.data_dir, options.pid) as session:
            run_mcp(session)
        return 0
    if options.out is not None and options.action != 'preview':
        parser.error('--out 仅用于 preview')
    arguments = {key: value for key, value in vars(options).items()
                 if key not in ('action', 'data_dir', 'pid', 'out') and value is not None}
    try:
        result = call(options.data_dir, options.pid, options.action, arguments)
        if options.out is not None:
            image = b64decode(result.pop('image_base64'), validate=True)
            with options.out.open('xb') as stream:
                stream.write(image)
            result['path'] = str(options.out.resolve())
    except (ControlError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # UTF-8 even when PowerShell launches Python with an ANSI output encoding.
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False) + '\n').encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
