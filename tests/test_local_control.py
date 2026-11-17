"""Exercise the control interface through HTTP, the CLI and MCP stdio."""
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Barrier, Timer

import cv2
import pytest
from PySide6.QtGui import QImage

import multicam.control as control
import multicam.control_client as client_module
from multicam.control_client import ControlError, ControlSession, _send, call
from test_audit_regressions import AuditWindow, app, wait_for, window


def background(app, function, *args):
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(function, *args)
        wait_for(app, job.done, timeout=10)
        return job.result()


def test_control_requires_opt_in_and_authentication(app, window):
    path = window.data_dir / ('control-%d.json' % os.getpid())
    assert not window.control_enabled.isChecked() and not path.exists()
    window.control_enabled.click()
    first = json.loads(path.read_text(encoding='utf-8'))
    port = int(first['url'].split(':')[-1].split('/')[0])

    def send(headers, action):
        connection = HTTPConnection('127.0.0.1', port, timeout=5)
        try:
            connection.request('POST', '/v1/control', json.dumps({'action': action}),
                               dict(headers, **{'Content-Type': 'application/json'}))
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    assert background(app, send, {}, 'snapshot')[0] == 401
    auth = {'Authorization': 'Bearer ' + first['token']}
    assert background(app, send, dict(auth, Origin='https://example.test'), 'snapshot')[0] == 403
    assert background(app, send, dict(auth, Host='example.test'), 'status')[0] == 403
    assert background(app, send, auth, 'arbitrary_command')[0] == 423
    owner = dict(auth, **{'X-CameraDesk-Controller': secrets.token_urlsafe(32)})
    assert background(app, send, owner, 'acquire_control')[0] == 200
    assert background(app, send, owner, 'arbitrary_command')[0] == 400
    assert not window.output_dir.exists()
    window.control_enabled.click()
    assert not path.exists()
    with pytest.raises(ControlError):
        call(window.data_dir, None, 'status', {})
    window.control_enabled.click()
    assert json.loads(path.read_text(encoding='utf-8'))['token'] != first['token']
    assert background(app, _send, path, 'control_info', {})['occupied'] is False
    window.close()
    wait_for(app, lambda: not window.isVisible())
    assert not path.exists()


def test_only_one_concurrent_controller_can_operate(app, window):
    window.control_enabled.setChecked(True)
    path = window.data_dir / ('control-%d.json' % os.getpid())
    claim_together = Barrier(2, timeout=5)

    def claim(controller):
        claim_together.wait()
        try:
            _send(path, 'acquire_control', {}, controller)
        except ControlError as exc:
            return exc.status
        return 200

    controllers = [secrets.token_urlsafe(32) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(claim, controller) for controller in controllers]
        wait_for(app, lambda: all(job.done() for job in jobs))
    results = [job.result() for job in jobs]
    assert sorted(results) == [200, 409]
    winner, other = (controllers[results.index(status)] for status in (200, 409))

    def command(controller, action):
        return background(app, _send, path, action, {}, controller)

    for action in ('status', 'preview', 'snapshot', 'release_control'):
        with pytest.raises(ControlError, match='HTTP 409'):
            command(other, action)
    assert not window.output_dir.exists()
    assert command(winner, 'preview')['camera_id'] == 'demo:0'
    assert command(winner, 'release_control')['released']
    assert command(other, 'acquire_control')['acquired']
    assert command(other, 'status')['selected'] == 'demo:0'
    with pytest.raises(ControlError, match='HTTP 409'):
        command(winner, 'status')


def test_idle_lease_expires_and_can_be_claimed_by_another_client(app, window, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(control, 'monotonic', lambda: now)
    window.control_enabled.setChecked(True)
    first, second = (ControlSession(window.data_dir) for _ in range(2))

    def status(session):
        return background(app, session.call, 'status', {})

    assert status(first)['selected'] == 'demo:0'
    now += 119
    assert status(first)['selected'] == 'demo:0'
    now += 119
    with pytest.raises(ControlError, match='HTTP 409'):
        status(second)
    now += 1
    assert status(second)['selected'] == 'demo:0'
    with pytest.raises(ControlError, match='HTTP 409'):
        status(first)
    assert background(app, second.release)
    assert status(first)['selected'] == 'demo:0'


def test_control_session_stays_bound_through_disconnect_and_pid_reuse(app, window, monkeypatch):
    window.control_enabled.setChecked(True)
    original_path = window.data_dir / ('control-%d.json' % os.getpid())
    client = ControlSession(window.data_dir)
    second = AuditWindow(window.data_dir)

    def status():
        return background(app, client.call, 'status', {})

    try:
        assert status()['selected'] == 'demo:0'
        window.control_enabled.setChecked(False)
        window.control_enabled.setChecked(True)
        assert status()['selected'] == 'demo:0'

        other_pid = os.getpid() + 1
        other = window.data_dir / ('control-%d.json' % other_pid)
        with monkeypatch.context() as pid:
            pid.setattr(control.os, 'getpid', lambda: other_pid)
            second.set_local_control(True)
        window.control_enabled.setChecked(False)
        for _ in range(2):
            with pytest.raises(ControlError):
                status()
        original_path.write_bytes(other.read_bytes())
        with pytest.raises(ControlError, match='原 Camera Desk 实例已退出'):
            status()
        assert background(app, _send, other, 'control_info', {})['occupied'] is False
    finally:
        background(app, client.release)
        original_path.unlink(missing_ok=True)
        second.close()
        wait_for(app, lambda: not second.close_timer.isActive())


@pytest.mark.parametrize('released_on_server', [False, True])
def test_unacknowledged_release_does_not_lock_out_owner(app, window, monkeypatch, released_on_server):
    window.control_enabled.setChecked(True)
    client = ControlSession(window.data_dir)

    def status(session):
        return background(app, session.call, 'status', {})

    def interrupted(path, action, arguments, *args, **kwargs):
        if action == 'release_control':
            if released_on_server:
                _send(path, action, arguments, *args, **kwargs)
            raise ControlError('temporary release failure', 504 if released_on_server else 503)
        return _send(path, action, arguments, *args, **kwargs)

    try:
        assert status(client)['selected'] == 'demo:0'
        with monkeypatch.context() as patch:
            patch.setattr(client_module, '_send', interrupted)
            assert background(app, client.release) is False
        assert status(client)['selected'] == 'demo:0'
        with pytest.raises(ControlError, match='HTTP 409'):
            status(ControlSession(window.data_dir))
    finally:
        assert background(app, client.release)
    assert background(app, call, window.data_dir, None, 'status', {})['selected'] == 'demo:0'


def test_control_submits_capture_and_reports_saved_media(app, window):
    window.control_enabled.setChecked(True)

    def command(action, **args):
        return background(app, call, window.data_dir, None, action, args)

    assert command('status')['selected'] == 'demo:0'
    result = command('preview', camera_id='demo:0')
    image = QImage.fromData(b64decode(result['image_base64']), 'JPEG')
    assert image.width() == 320 and image.height() == 240
    command('set_format', width=640, height=360, fps=25)
    session = window.sessions['demo:0']
    wait_for(app, lambda: session.config.width == 640 and session.tile.view.image.width() == 640)
    with pytest.raises(ControlError, match='400'):
        command('set_format', width=True, height=360)
    photo = command('snapshot')['path']
    wait_for(app, lambda: session.last_photo == photo)
    assert QImage(photo).size().toTuple() == (640, 360)
    video = command('start_recording')['path']
    wait_for(app, lambda: session.recording)
    with pytest.raises(ControlError, match='409'):
        command('start_recording')
    assert command('stop_recording')['path'] == video
    wait_for(app, lambda: session.last_video == video)
    state = command('status')['cameras'][0]
    assert not state['recording'] and state['last_video'] == video and state['last_photo'] == photo
    capture = cv2.VideoCapture(video)
    try:
        assert capture.read()[0]
    finally:
        capture.release()


def test_photo_status_tracks_each_completion_and_failure(app, window, monkeypatch):
    window.control_enabled.setChecked(True)

    def command(action, **args):
        return background(app, call, window.data_dir, None, action, args)

    photos = [command('snapshot') for _ in range(2)]
    assert photos[0]['request_id'] != photos[1]['request_id']
    for photo in photos:
        def lookup():
            return command('photo_status', request_id=photo['request_id'])
        wait_for(app, lambda: lookup()['state'] == 'saved')
        assert lookup()['path'] == photo['path'] and Path(photo['path']).is_file()

    def fail_encode(*_):
        raise RuntimeError('simulated encoding failure')

    monkeypatch.setattr(cv2, 'imencode', fail_encode)
    failed = command('snapshot')
    wait_for(app, lambda: command('photo_status', request_id=failed['request_id'])['state'] == 'failed')
    failure = command('photo_status', request_id=failed['request_id'])
    assert 'simulated encoding failure' in failure['error'] and not Path(failed['path']).exists()
    assert command('photo_status', request_id=photos[0]['request_id'])['state'] == 'saved'


def test_cli_and_mcp_connect_to_running_window(app, window, tmp_path):
    window.control_enabled.setChecked(True)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'camera-control.py'
    prefix = [sys.executable, str(script), '--data-dir', str(window.data_dir)]

    def run(action, *args):
        return subprocess.run(prefix + [action, *args], capture_output=True, timeout=8, cwd=tmp_path)

    def preview_from_cli():
        return run('preview', '--camera', 'demo:0', '--out', str(tmp_path / 'frame.jpg'))

    result = background(app, preview_from_cli)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['camera_id'] == 'demo:0'
    assert not QImage(str(tmp_path / 'frame.jpg')).isNull()
    photo_result = background(app, run, 'snapshot', '--camera', 'demo:0')
    assert photo_result.returncode == 0, photo_result.stderr
    photo_id = json.loads(photo_result.stdout)['request_id']
    lookup = background(app, run, 'photo_status', '--request-id', photo_id)
    assert lookup.returncode == 0 and json.loads(lookup.stdout)['request_id'] == photo_id

    def launch_mcp():
        messages = [
            {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25',
             'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
            {'method': 'notifications/initialized'},
            {'id': 2, 'method': 'tools/list'},
            {'id': 3, 'method': 'tools/call', 'params': {'name': 'camera_desk_preview'}},
        ]
        with subprocess.Popen(prefix + ['mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=tmp_path) as bridge:
            watchdog = Timer(8, bridge.kill)
            watchdog.start()
            try:
                def send(batch):
                    wire = ''.join(json.dumps(dict(message, jsonrpc='2.0')) + '\n' for message in batch)
                    bridge.stdin.write(wire.encode('utf-8'))
                    bridge.stdin.flush()
                    return [json.loads(bridge.stdout.readline()) for message in batch if 'id' in message]

                replies = send(messages)
                blocked = run('status')
                assert blocked.returncode == 1 and b'HTTP 409' in blocked.stderr
                replies += send([{'id': 4, 'method': 'tools/call', 'params': {'name': 'camera_desk_release_control'}}])
                assert run('status').returncode == 0
                replies += send([{'id': 5, 'method': 'tools/call', 'params': {
                    'name': 'camera_desk_photo_status', 'arguments': {'request_id': photo_id}}}])
                bridge.stdin.close()
                assert bridge.wait(timeout=3) == 0, bridge.stderr.read()
                assert run('status').returncode == 0
                return replies
            finally:
                watchdog.cancel()
                if bridge.poll() is None:
                    bridge.kill()

    init, listing, preview, released, reacquired = background(app, launch_mcp)
    assert init['result']['protocolVersion'] == '2025-11-25'
    names = {tool['name'] for tool in listing['result']['tools']}
    assert {'camera_desk_snapshot', 'camera_desk_photo_status', 'camera_desk_release_control'} <= names
    content = preview['result']['content'][0]
    assert content['type'] == 'image' and content['mimeType'] == 'image/jpeg'
    assert not QImage.fromData(b64decode(content['data']), 'JPEG').isNull()
    assert json.loads(released['result']['content'][0]['text']) == {'released': True}
    assert json.loads(reacquired['result']['content'][0]['text'])['request_id'] == photo_id


def test_mcp_rejects_invalid_and_repeated_request_ids():
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'camera-control.py'
    ids = [None, True, 1.5, {}, 1, 1, '1', '1']
    wire = ''.join(json.dumps(dict(jsonrpc='2.0', method='ping', id=key)) + '\n' for key in ids)
    result = subprocess.run([sys.executable, str(script), 'mcp'], input=wire.encode('utf-8'),
                            capture_output=True, timeout=5, check=True)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply.get('error', {}).get('code') for reply in replies] == [-32600] * 4 + [None, -32600, None, -32600]
    assert replies[4] == {'jsonrpc': '2.0', 'id': 1, 'result': {}}
    assert replies[6] == {'jsonrpc': '2.0', 'id': '1', 'result': {}}
