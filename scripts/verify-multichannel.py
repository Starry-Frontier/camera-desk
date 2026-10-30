"""Exercise the production GUI/recording pipeline with 1-8 synthetic sources.

No camera is opened. Output is isolated from the portable application's data.
Example: python scripts/verify-multichannel.py --output-dir verification/run --counts 1 2 3 4 5 6 7 8
"""
import argparse
import ctypes
import gc
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import traceback

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from multicam import __version__
from multicam.capture import CameraWorker, _demo_frame
from multicam.devices import CameraDevice
from multicam.save_dialog import BatchSaveDialog
import multicam.ui as ui


class ObservedWorker(CameraWorker):
    """Measure the unmodified writer without replacing sources or encoders."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observation = {'samples': [], 'written_frames': 0, 'repeat_frames': 0}
        self._pending_sample = 0
        self._last_written_sample = -1

    def _begin_recording(self, request):
        super()._begin_recording(request)
        if self._writer is not None:
            self.observation['started'] = self._record_started
            self.observation['path'] = str(request.path)

    def _write_recording_until(self, target):
        before = self._record_count
        super()._write_recording_until(target)
        written = self._record_count - before
        if written:
            fresh = int(self._pending_sample != self._last_written_sample)
            self.observation['repeat_frames'] += written - fresh
            self._last_written_sample = self._pending_sample
            self.observation['written_frames'] += written

    def _record_frame(self, frame, now):
        sample = len(self.observation['samples'])
        self.observation['samples'].append(now)
        super()._record_frame(frame, now)
        # The production writer retains this sample for the next clock slot.
        self._pending_sample = sample

    def _finish_recording(self, pad=True):
        if self._writer is not None:
            self.observation['stop_clock'] = time.monotonic()
            super()._finish_recording(pad=pad)
            self.observation['finalized'] = time.monotonic()


class SimulationWindow(ui.MainWindow):
    def refresh_devices(self):
        # Device discovery is irrelevant to synthetic capture; do not touch USB.
        self.device_hint.setText('模拟测试：不连接实体摄像头')


def memory_mib():
    if os.name != 'nt':
        return None

    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return counters.WorkingSetSize / (1024 * 1024)


def wait_for(app, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.002)
    raise TimeoutError('Timed out waiting for an asynchronous recording operation')


def percentile(values, q):
    return float(np.percentile(values, q)) if len(values) else 0.0


def verify_video(path, worker, width, height, fps, expected_headers, channel):
    observation = worker.observation
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise AssertionError('Video cannot be opened: ' + str(path))
    frames = 0
    identical_neighbors = 0
    samples = []
    last = None
    try:
        stored_fps = cap.get(cv2.CAP_PROP_FPS)
        declared_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        assert abs(stored_fps - fps) < .01, 'Wrong video FPS'
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            assert frame.shape[:2] == (height, width), 'Wrong frame dimensions'
            if last is not None and path.suffix.lower() == '.avi' and np.array_equal(frame, last):
                identical_neighbors += 1
            if frames == 0 or frames == observation['written_frames'] // 2:
                samples.append(frame[:expected_headers[0].shape[0]].copy())
            last = frame
            frames += 1
        assert frames > 0 and frames == observation['written_frames'], 'Missing or undecodable video frames'
        samples.append(last[:expected_headers[0].shape[0]])
        for header in samples:
            scores = [float(np.mean(cv2.absdiff(header, expected))) for expected in expected_headers]
            assert int(np.argmin(scores)) == channel, 'Video channel identity mismatch'
        clock_seconds = observation['stop_clock'] - observation['started']
        duration = frames / stored_fps
        assert abs(duration - clock_seconds) <= max(.15, 2 / fps), 'Video duration differs from elapsed recording time'
        timestamps = observation['samples']
        actual_fps = (len(timestamps) - 1) / (timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0
        gaps = np.diff(timestamps) * 1000 if len(timestamps) > 1 else []
        repeats = observation['repeat_frames']
        return {
            'channel': channel + 1, 'file': str(path), 'container': path.suffix,
            'width': width, 'height': height, 'declared_fps': stored_fps,
            'declared_frames': declared_frames, 'decoded_frames': frames,
            'recording_seconds': clock_seconds, 'video_seconds': duration,
            'duration_error_seconds': abs(duration - clock_seconds),
            'source_frames': len(timestamps), 'measured_source_fps': actual_fps,
            'effective_video_updates_fps': (frames - repeats) / duration,
            'repeat_frames': repeats, 'repeat_percent': 100 * repeats / frames,
            'identical_decoded_neighbors_avi': identical_neighbors if path.suffix.lower() == '.avi' else None,
            'source_gap_p95_ms': percentile(gaps, 95), 'source_gap_max_ms': max(gaps, default=0),
            'first_middle_last_channel_identity': True,
            'file_mib': path.stat().st_size / (1024 * 1024),
        }
    finally:
        cap.release()


def run_scenario(app, root, count, args):
    root.mkdir(parents=True)
    config = {'width': args.width, 'height': args.height, 'fps': args.fps}
    (root / 'settings.json').write_text(json.dumps({
        'cameras': {'demo:%d' % i: {'config': config} for i in range(count)},
    }), encoding='utf-8')
    window = SimulationWindow(root)
    window.show()
    errors, photos, events, heartbeat, memory = [], [], [], [], []
    workers = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
    report = {'channels': count, 'width': args.width, 'height': args.height,
              'target_fps': args.fps, 'requested_seconds': args.seconds,
              'format': args.format, 'functional_pass': False, 'videos': []}
    try:
        for i in range(count):
            key = 'demo:%d' % i
            window.add_camera(CameraDevice(i, '模拟通道 %02d' % (i + 1), key), demo=True)
            worker = window.sessions[key].worker
            workers.append(worker)
            worker.error.connect(lambda text, i=i: errors.append({'channel': i + 1, 'message': text}))
            worker.snapshot_saved.connect(photos.append)
            worker.recording_changed.connect(lambda active, path, i=i: events.append(
                {'channel': i + 1, 'active': active, 'path': path, 'at': time.monotonic()}))
        wait_for(app, lambda: all(s.ready and s.stats.get('fps', 0) > 0 for s in window.sessions.values()))
        if args.format == 'mixed':
            for i, key in enumerate(window.sessions):
                window.remember_media_format('video', ('.avi', '.mp4', '.mkv')[i % 3])
                window.toggle_record(key, prompt=False)
        else:
            window.remember_media_format('video', '.' + args.format)
            window.record_all(prompt=False)
        wait_for(app, lambda: all(s.recording for s in window.sessions.values()))
        timer.start()
        cpu_start, run_start = time.process_time(), time.monotonic()
        last_resource = last_progress = run_start
        round_index = 0
        formats = ('.png', '.jpg', '.bmp', '.tif', '.webp')
        photo_rounds = min(args.photo_rounds, len(formats))
        while True:
            app.processEvents()
            now = time.monotonic()
            elapsed = now - run_start
            if elapsed >= args.seconds:
                break
            assert not errors, 'Worker error: ' + str(errors)
            assert all(s.recording for s in window.sessions.values()), 'A recording stopped unexpectedly'
            if round_index < photo_rounds and elapsed >= args.seconds * (round_index + 1) / (photo_rounds + 1):
                window.remember_media_format('photo', formats[round_index])
                window.all_photo.click()
                if round_index == 0:
                    window.grab().save(str(root / 'preview.png'))
                round_index += 1
            if now - last_resource >= .5:
                sample = memory_mib()
                if sample is not None:
                    memory.append(sample)
                last_resource = now
            if now - last_progress >= 15:
                print('%dch: %.0f/%.0fs recorded, %d photos' % (count, elapsed, args.seconds, len(photos)), flush=True)
                last_progress = now
            time.sleep(.002)
        elapsed = time.monotonic() - run_start
        cpu_seconds = time.process_time() - cpu_start
        report['cpu_percent_one_core'] = 100 * cpu_seconds / elapsed
        report['cpu_percent_all_logical_cores'] = 100 * cpu_seconds / elapsed / (os.cpu_count() or 1)
        report['peak_working_set_mib'] = max(memory, default=0)
        gaps = np.diff(heartbeat) * 1000
        report['gui_heartbeat_p95_ms'] = percentile(gaps, 95)
        report['gui_heartbeat_max_ms'] = max(gaps, default=0)
        report['all_channels_active_seconds'] = elapsed
        starts = [w.observation['started'] for w in workers]
        report['start_skew_ms'] = (max(starts) - min(starts)) * 1000
        window.all_stop.click()
        wait_for(app, lambda: not any(s.recording or s.pending_record for s in window.sessions.values()), seconds=60)
        wait_for(app, lambda: len(photos) == count * photo_rounds, seconds=60)
        timer.stop()
        assert not errors, 'Worker error: ' + str(errors)
        assert len(events) == count * 2 and all(
            [e['active'] for e in events if e['channel'] == i + 1] == [True, False]
            for i in range(count)), 'Recording restarted or stopped unexpectedly'
        assert all(e['path'] for e in events), 'A recording was not saved'
        assert len(set(photos)) == count * photo_rounds, 'A photo was overwritten or not saved'
        for photo in photos:
            image = cv2.imdecode(np.fromfile(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape[:2] == (args.height, args.width), 'Invalid snapshot'
            assert Path(photo).parent == window.output_dir, 'Photos did not share a directory'
        window.close()
        wait_for(app, lambda: all(not w.isRunning() for w in workers) and not window.isVisible(), seconds=60)
        assert not list((root / 'captures').glob('.recording-*')), 'An unfinished video remains'
        print('%dch: recording finished; fully decoding every video frame' % count, flush=True)
        scale = max(.35, min(args.width, args.height) / 700)
        header_height = max(12, args.width // 32) + int(45 * scale)
        headers = [_demo_frame(args.width, args.height, i, 0)[:header_height] for i in range(count)]
        for i, worker in enumerate(workers):
            path = Path(worker.observation['path'])
            report['videos'].append(verify_video(path, worker, args.width, args.height, args.fps, headers, i))
        report['photos'] = len(photos)
        report['photo_formats'] = sorted({Path(p).suffix for p in photos})
        report['errors'] = errors
        report['functional_pass'] = True
        # Quality criterion is separate from successful file creation/decoding.
        report['realtime_pass'] = all(v['measured_source_fps'] >= args.fps * .95
                                      and v['repeat_percent'] <= 5 for v in report['videos'])
    except Exception:
        report['exception'] = traceback.format_exc()
        report['errors'] = errors
        print(report['exception'], flush=True)
    finally:
        timer.stop()
        window.close()
        wait_for(app, lambda: all(not w.isRunning() for w in workers) and not window.isVisible(), seconds=60)
        window.deleteLater()
        app.processEvents()
    (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('%dch: functional=%s, realtime=%s' % (count, report['functional_pass'], report.get('realtime_pass')), flush=True)
    gc.collect()
    return report


def write_summary(root, result):
    rows = ['# 多通道同时录像模拟测试', '',
            '软件版本：%s。模拟画面通过正式界面和采集／录像流程生成；未连接实体摄像头。' % __version__, '',
            '| 通道数 | 尺寸 / 目标 fps | 同时录像秒数 | 可完整解码视频 | 照片 | 模拟源更新 fps | 录像有效更新 fps | 补帧比例 | 功能 | 实时性 |',
            '|---:|---|---:|---:|---:|---|---|---|---|---|']
    for r in result['scenarios']:
        videos = r['videos']
        fps = [v['measured_source_fps'] for v in videos] or [0]
        effective = [(v['decoded_frames'] - v['repeat_frames']) / v['video_seconds'] for v in videos] or [0]
        repeats = [v['repeat_percent'] for v in videos] or [0]
        rows.append('| %d | %d×%d / %g | %.1f | %d | %d | %.2f–%.2f | %.2f–%.2f | %.2f%%–%.2f%% | %s | %s |' % (
            r['channels'], r['width'], r['height'], r['target_fps'], r.get('all_channels_active_seconds', 0),
            len(videos), r.get('photos', 0), min(fps), max(fps), min(effective), max(effective), min(repeats), max(repeats),
            '通过' if r['functional_pass'] else '失败', '通过' if r.get('realtime_pass') else '未达标'))
    rows += ['', '实时性标准：各路实际画面更新率至少为目标的 95%，且按录制时钟重复补写的帧不超过 5%。',
             '视频标注 fps 与实际画面更新率分别统计。补帧统计来自实际写入调用，而非只读取视频头。', '',
             '“录像有效更新 fps”按视频中使用的新采样数计算，等于（写入帧数－重复补帧数）／视频时长；',
             '模拟源更新率接近 30 fps 时，录制时钟分配仍可能使部分采样未被使用、另一些采样重复写入。', '',
             '每个视频均逐帧解码，核对帧数、尺寸、时长，并检查首／中／末画面的通道标识。',
             '录像期间点击“全部拍照”，检查照片数量、尺寸、格式和共同目录；核对每路只开始和停止一次。', '',
             '本结果仅表示当前电脑上的软件模拟负载；不代表 8 台实体 USB 摄像头、USB 带宽或其他电脑的性能。', '',
             '详细资源占用、逐路结果、视频和预览见同目录 JSON 与各场景子目录。']
    (root / 'REPORT.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--counts', type=int, nargs='+', default=list(range(1, 9)))
    parser.add_argument('--seconds', type=float, default=15)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--format', choices=['avi', 'mp4', 'mkv', 'mixed'], default='avi')
    parser.add_argument('--photo-rounds', type=int, choices=range(1, 6), default=1)
    args = parser.parse_args()
    if any(n < 1 or n > 8 for n in args.counts):
        parser.error('Channel count must be between 1 and 8')
    if args.seconds <= 0 or args.width % 2 or args.height % 2:
        parser.error('Use positive duration and even frame dimensions')
    root = args.output_dir.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error('Use a new, empty output directory')
    root.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    if not QFontDatabase.families() and os.name == 'nt':
        for name in ('segoeui.ttf', 'msyh.ttc'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    app.setQuitOnLastWindowClosed(False)
    dialogs = []
    def reject_dialog(*args, **kwargs):
        dialogs.append('Unexpected save/confirmation dialog')
        raise AssertionError(dialogs[-1])
    QFileDialog.getSaveFileName = reject_dialog
    BatchSaveDialog.exec = reject_dialog
    QMessageBox.question = reject_dialog
    ui.CameraWorker = ObservedWorker
    result = {'version': __version__, 'platform': platform.platform(), 'python': platform.python_version(),
              'processor': os.environ.get('PROCESSOR_IDENTIFIER'), 'logical_processors': os.cpu_count(),
              'opencv': cv2.__version__, 'headless_qt_platform': os.environ['QT_QPA_PLATFORM'],
              'free_disk_gib_before': shutil.disk_usage(root).free / (1024 ** 3),
              'source': 'Production MainWindow and CameraWorker, synthetic DEMO inputs', 'scenarios': []}
    for count in args.counts:
        name = '%02dch-%dx%d-%gfps-%s' % (count, args.width, args.height, args.fps, args.format)
        print('Starting %s: %.0f seconds' % (name, args.seconds), flush=True)
        result['scenarios'].append(run_scenario(app, root / name, count, args))
        result['unexpected_dialogs'] = dialogs
        (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        write_summary(root, result)
    return 0 if all(r['functional_pass'] for r in result['scenarios']) and not dialogs else 1


if __name__ == '__main__':
    sys.exit(main())
