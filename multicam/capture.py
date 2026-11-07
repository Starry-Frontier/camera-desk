"""Independent camera acquisition and image processing, with no GUI-thread I/O.

The worker owns its OpenCV handles for their entire lifetime. Public methods
enqueue commands; they may be called from the main thread. Acknowledge each
``frame_ready`` delivery with ``acknowledge_preview`` to release preview credit.
"""
from __future__ import annotations

import math
import os
import queue
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .formats import enumerate_camera_modes
from .hardware import read_camera_zoom
from .media_formats import format_for_extension
from .saving import path_identity


# OpenCV DirectShow uses one process-wide videoInput object. Serialize graph
# setup/teardown while leaving acquisition from distinct devices independent.
_DEVICE_CONTROL_LOCK = threading.RLock()
_PHOTO_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='CameraPhoto')
_PHOTO_SLOTS = threading.BoundedSemaphore(32)
_PHOTO_TARGET_LOCK = threading.Lock()
_PHOTO_TARGETS = set()


@dataclass
class CameraConfig:
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    native_only: bool = False

    def validated(self):
        width, height, fps = int(self.width), int(self.height), float(self.fps)
        if not (16 <= width <= 7680 and 16 <= height <= 4320):
            raise ValueError("分辨率须在 16×16 至 7680×4320 范围内。")
        if not math.isfinite(fps) or not 1 <= fps <= 120:
            raise ValueError("帧率须在 1 至 120 fps 范围内。")
        return CameraConfig(width, height, fps, bool(self.native_only))


@dataclass
class ImageSettings:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    sharpness: float = 0.0
    mirror: bool = False
    flip_vertical: bool = False

    def validated(self):
        values = (self.brightness, self.contrast, self.saturation, self.sharpness)
        if not all(math.isfinite(float(v)) for v in values):
            raise ValueError("图像参数必须是有限数值。")
        return ImageSettings(
            max(-100.0, min(100.0, float(self.brightness))),
            max(0.0, min(3.0, float(self.contrast))),
            max(0.0, min(3.0, float(self.saturation))),
            max(0.0, min(3.0, float(self.sharpness))),
            bool(self.mirror), bool(self.flip_vertical),
        )


@dataclass(frozen=True)
class SaveRequest:
    path: str
    overwrite: bool = False
    duration_seconds: float = 0.0
    photo_identity: str = ''


def _release_photo_target(request):
    with _PHOTO_TARGET_LOCK:
        _PHOTO_TARGETS.discard(request.photo_identity)


def apply_image_settings(frame: np.ndarray, settings: ImageSettings) -> np.ndarray:
    """Apply the same BGR transformation for previews and saved media."""
    result = frame
    if settings.contrast != 1 or settings.brightness != 0:
        # A LUT avoids a full-resolution float32 RGB intermediate per camera.
        lut = np.clip(
            (np.arange(256, dtype=np.float32) - 127.5) * settings.contrast
            + 127.5 + settings.brightness * 2.55, 0, 255,
        ).astype(np.uint8)
        result = cv2.LUT(result, lut)
    if settings.saturation != 1:
        hsv = cv2.cvtColor(result, cv2.COLOR_BGR2HSV)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1].astype(np.float32) * settings.saturation, 0, 255).astype(np.uint8)
        result = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    if settings.sharpness > 0:
        blurred = cv2.GaussianBlur(result, (0, 0), 1.0)
        result = cv2.addWeighted(result, 1 + settings.sharpness, blurred, -settings.sharpness, 0)
    if settings.mirror or settings.flip_vertical:
        axis = -1 if settings.mirror and settings.flip_vertical else (1 if settings.mirror else 0)
        result = cv2.flip(result, axis)
    return np.ascontiguousarray(result)


def _demo_frame(width: int, height: int, camera: int, seconds: float) -> np.ndarray:
    """An explicitly labelled synthetic source, usable without a camera."""
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = (35, 25, 18) if camera % 2 == 0 else (30, 20, 35)
    unit = max(32, int(min(width, height) / 8))
    grid_color = (55, 44, 34) if camera % 2 == 0 else (56, 36, 57)
    for x in range(0, width, unit):
        cv2.line(image, (x, 0), (x, height), grid_color, 1)
    for y in range(0, height, unit):
        cv2.line(image, (0, y), (width, y), grid_color, 1)
    radius = max(14, int(min(width, height) * .13))
    cx = int(width * (.50 + .25 * math.sin(seconds * .7 + camera * 1.3)))
    cy = int(height * (.52 + .17 * math.cos(seconds * .9 + camera)))
    accent = (100, 208, 214) if camera % 2 == 0 else (208, 151, 115)
    cv2.circle(image, (cx, cy), radius, accent, -1, cv2.LINE_AA)
    cv2.circle(image, (cx, cy), int(radius * 1.30), accent, 2, cv2.LINE_AA)
    scale = max(.35, min(width, height) / 700)
    pad = max(12, width // 32)
    cv2.putText(image, "DEMO / CAMERA %02d" % (camera + 1), (pad, pad + int(35 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (225, 235, 236), 2, cv2.LINE_AA)
    cv2.putText(image, "%d x %d    %06.1fs" % (width, height, seconds), (pad, height - pad),
                cv2.FONT_HERSHEY_SIMPLEX, scale * .75, (152, 174, 181), 1, cv2.LINE_AA)
    # Asymmetric L marker makes mirror / vertical flip immediately verifiable.
    cv2.rectangle(image, (pad, height // 2), (pad + unit, height // 2 + unit), accent, 2)
    cv2.putText(image, "L", (pad + unit // 4, height // 2 + unit * 3 // 4),
                cv2.FONT_HERSHEY_SIMPLEX, scale, accent, 2, cv2.LINE_AA)
    return image


class CameraWorker(QThread):
    frame_ready = Signal(QImage)
    stats_changed = Signal(dict)
    status_changed = Signal(str)
    error = Signal(str)
    snapshot_saved = Signal(str)
    recording_changed = Signal(bool, str)
    hardware_changed = Signal(str, object, bool)
    zoom_available = Signal(dict)
    formats_available = Signal(list)
    format_changed = Signal(bool, dict, str)

    def __init__(self, device_id: Union[int, str], config: Optional[CameraConfig] = None, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self._config = replace(config or CameraConfig()).validated()
        self._settings = ImageSettings()
        self._commands = queue.Queue()
        self._stop_event = threading.Event()
        self._preview_credit = threading.Event()
        self._preview_credit.set()
        self._capture = None
        self._frame = None
        self._writer = None
        self._record_path = None
        self._record_temp = None
        self._record_started = 0.0
        self._record_count = 0
        self._record_fps = 30.0
        self._record_last_frame = None
        self._record_last_time = 0.0
        self._record_size = None
        self._record_overwrite = False
        self._record_deadline = None
        self._zoom = {'supported': False, 'reason': '演示机位不支持硬件变焦'}
        self._hardware = {}
        self._hardware_requests = {}
        self._modes = []
        self._primed_frame = None
        self._pending_snapshots = []
        self._photo_jobs = []
        self._pending_record = None
        self._frame_times = deque(maxlen=120)
        self._actual_width = self._config.width
        self._actual_height = self._config.height
        self._device_fps = self._config.fps
        self._demo = isinstance(device_id, str) and device_id.startswith("demo:")
        self._demo_index = int(device_id.split(":", 1)[1]) if self._demo else 0

    def acknowledge_preview(self):
        self._preview_credit.set()

    def update_image(self, settings: ImageSettings):
        self._commands.put(("image", replace(settings)))

    def set_format(self, width: int, height: int, fps: float, native_only=False):
        self._commands.put(("format", CameraConfig(width, height, fps, native_only)))

    def set_hardware(self, name: str, value):
        self._commands.put(("hardware", (str(name), value)))

    @staticmethod
    def snapshot_target_busy(path):
        identity = path_identity(path)
        with _PHOTO_TARGET_LOCK:
            return identity in _PHOTO_TARGETS

    def take_snapshot(self, path, overwrite=False):
        try:
            identity = path_identity(path)
        except (OSError, RuntimeError, ValueError) as exc:
            self.error.emit('无法确认照片保存路径：%s' % exc)
            return False
        # Reserve on submission, before either camera has started encoding.
        # Distinct paths still save in parallel in the photo pool.
        with _PHOTO_TARGET_LOCK:
            if self._stop_event.is_set() or not self.isRunning():
                message = '机位已停止，无法拍照。'
            elif identity in _PHOTO_TARGETS:
                message = '同名照片正在保存，请稍候或使用其他文件名。'
            else:
                _PHOTO_TARGETS.add(identity)
                self._commands.put(("snapshot", SaveRequest(str(path), bool(overwrite), photo_identity=identity)))
                return True
        self.error.emit(message)
        return False

    def start_recording(self, path, overwrite=False, duration_seconds=0):
        duration = float(duration_seconds)
        if not math.isfinite(duration) or duration < 0 or duration > 604800:
            raise ValueError('录像时长须为 0（不限）至 604800 秒。')
        self._commands.put(("record", SaveRequest(str(path), bool(overwrite), duration)))

    def stop_recording(self):
        self._commands.put(("stop_record", None))

    def shutdown(self):
        self._stop_event.set()
        self.requestInterruption()

    def run(self):
        self.status_changed.emit("正在连接摄像头…" if not self._demo else "正在启动演示…")
        last_preview = last_stats = 0.0
        # Python 3.8 on Windows uses a coarse tick counter for monotonic().
        # The performance counter is also monotonic, with sub-frame precision.
        started = time.perf_counter()
        next_demo = started
        failures = 0
        try:
            if not self._demo:
                try:
                    with _DEVICE_CONTROL_LOCK:
                        self._zoom = read_camera_zoom(int(self.device_id))
                except Exception:
                    self._zoom = {'supported': False, 'reason': '未能读取变焦范围，设备可继续预览'}
                try:
                    with _DEVICE_CONTROL_LOCK:
                        self._modes = enumerate_camera_modes(int(self.device_id))
                except Exception as exc:
                    self.status_changed.emit('未能读取设备格式，可手动输入分辨率：%s' % exc)
                self.formats_available.emit([asdict(mode) for mode in self._modes])
            self._apply_format(self._config, initial=True)
            if self._zoom.get('supported'):
                current = self._capture.get(cv2.CAP_PROP_ZOOM)
                self._zoom['current'] = int(current) if math.isfinite(current) and self._zoom['minimum'] <= current <= self._zoom['maximum'] else None
            self.zoom_available.emit(dict(self._zoom))
            self.status_changed.emit("演示预览中" if self._demo else "预览中")
            while not self._stop_event.is_set():
                self._drain_commands()
                if self._writer is not None and self._record_deadline is not None and time.perf_counter() >= self._record_deadline:
                    self._finish_recording()
                if self._stop_event.is_set():
                    break
                now = time.perf_counter()
                if self._demo:
                    delay = next_demo - now
                    if delay > 0:
                        self._stop_event.wait(min(delay, .01))
                        continue
                    raw = _demo_frame(self._config.width, self._config.height, self._demo_index, now - started)
                    next_demo = max(next_demo + 1.0 / self._config.fps, now)
                    ok = True
                elif self._primed_frame is not None:
                    raw, self._primed_frame = self._primed_frame, None
                    ok = True
                else:
                    ok, raw = self._capture.read()
                if not ok or raw is None or raw.size == 0:
                    failures += 1
                    if failures >= 15:
                        raise RuntimeError("摄像头已断开或无法读取画面。请重新连接后移除此机位，再重新添加。")
                    self._stop_event.wait(.04)
                    continue
                failures = 0
                now = time.perf_counter()
                if raw.ndim == 2:
                    raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
                elif raw.shape[2] == 4:
                    raw = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
                self._actual_height, self._actual_width = raw.shape[:2]
                if (self._actual_width, self._actual_height) != (self._config.width, self._config.height):
                    if self._config.native_only:
                        self._frame = None
                        raise RuntimeError('设备采集尺寸变为 %d × %d，已停止该机位以遵守原生分辨率限制。'
                                           % (self._actual_width, self._actual_height))
                    interpolation = cv2.INTER_AREA if self._config.width <= self._actual_width and self._config.height <= self._actual_height else cv2.INTER_CUBIC
                    raw = cv2.resize(raw, (self._config.width, self._config.height), interpolation=interpolation)
                self._frame = apply_image_settings(raw, self._settings)
                self._frame_times.append(now)
                if self._pending_record:
                    path, self._pending_record = self._pending_record, None
                    self._begin_recording(path)
                self._fulfil_snapshots()
                if self._writer is not None:
                    if self._record_deadline is not None and now >= self._record_deadline:
                        self._finish_recording()
                    else:
                        self._record_frame(self._frame, now)
                if now - last_preview >= .05 and self._preview_credit.is_set():
                    self._emit_preview(self._frame)
                    last_preview = now
                if now - last_stats >= .5:
                    self._emit_stats(now)
                    last_stats = now
        except Exception as exc:
            if not self._stop_event.is_set():
                self.error.emit(str(exc))
                self.status_changed.emit("连接异常")
        finally:
            # Close admission under the same lock used to enqueue snapshots.
            with _PHOTO_TARGET_LOCK:
                self._stop_event.set()
            try:
                try:
                    self._finish_recording()
                finally:
                    self._release_capture()
            except Exception as exc:
                self.error.emit('关闭机位时释放资源失败：%s' % exc)
            # Complete accepted photos using the last available frame before
            # destroying this worker. Do not start new recordings while closing.
            while True:
                try:
                    command, value = self._commands.get_nowait()
                except queue.Empty:
                    break
                if command == 'snapshot':
                    self._pending_snapshots.append(value)
            if self._frame is not None:
                self._fulfil_snapshots()
            for job in self._photo_jobs:
                job.result()
            self._photo_jobs.clear()
            if self._pending_snapshots or self._pending_record:
                for request in self._pending_snapshots:
                    _release_photo_target(request)
                self.error.emit("摄像头已停止，尚未完成的拍照或录像请求已取消。")
            self._pending_snapshots.clear()
            self._pending_record = None
            self._frame = None
            self.status_changed.emit("已停止")

    def _drain_commands(self):
        # Bound each pass so rapidly dragging a slider cannot starve acquisition.
        for _ in range(128):
            try:
                command, value = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if command == "image":
                    self._settings = value.validated()
                elif command == "format":
                    if self._writer is not None or self._pending_record:
                        raise ValueError("请先停止录像，再切换分辨率或帧率。")
                    self._apply_format(value.validated())
                    self._frame = None
                    self._frame_times.clear()
                    self._emit_stats(time.perf_counter())
                elif command == "hardware":
                    self._apply_hardware(*value)
                elif command == "snapshot":
                    if len(self._pending_snapshots) >= 8:
                        _release_photo_target(value)
                        raise ValueError("拍照请求过多，请等待保存完成。")
                    self._pending_snapshots.append(value)
                elif command == "record":
                    if self._writer is not None or self._pending_record:
                        raise ValueError("此机位正在录像。")
                    self._pending_record = value
                elif command == "stop_record":
                    if self._pending_record:
                        self.recording_changed.emit(False, "")
                    self._pending_record = None
                    self._finish_recording()
            except Exception as exc:
                self.error.emit(str(exc))
                if command == 'format':
                    self.format_changed.emit(False, asdict(self._config), str(exc))

    def _apply_format(self, config: CameraConfig, initial=False):
        if self._demo:
            self._config = config
            self._actual_width, self._actual_height = config.width, config.height
            self._device_fps = config.fps
            self.format_changed.emit(True, asdict(config), '')
            return
        previous = replace(self._config)
        self._release_capture()
        try:
            capture, frame = self._open_format(config, require_exact=config.native_only)
        except Exception as exc:
            if initial:
                raise
            try:
                capture, frame = self._open_format(previous, require_exact=previous.native_only)
                self._accept_capture(capture, frame, previous)
            except Exception as restore_error:
                raise RuntimeError('切换失败，原画面恢复失败：%s；%s' % (exc, restore_error)) from restore_error
            message = '分辨率切换失败，已恢复原画面：%s' % exc
            self.error.emit(message)
            self.format_changed.emit(False, asdict(previous), message)
            return
        self._accept_capture(capture, frame, config)
        self._config = config
        message = ''
        if (frame.shape[1], frame.shape[0]) != (config.width, config.height):
            message = '输出 %d × %d（设备采集 %d × %d，软件缩放）。' % (config.width, config.height, frame.shape[1], frame.shape[0])
        self.format_changed.emit(True, asdict(config), message)

    def _release_capture(self):
        if self._capture is not None:
            with _DEVICE_CONTROL_LOCK:
                self._capture.release()
            self._capture = None

    def _open_format(self, config, require_exact):
        # Each candidate gets a fresh graph. FPS must be set BEFORE dimensions
        # and FourCC: OpenCV's DirectShow FPS setter rebuilds the graph without
        # preserving FourCC. Native dimensions and output scaling are reported
        # separately; native_only refuses any device fallback.
        matching = [m for m in self._modes if (m.width, m.height) == (config.width, config.height)]
        codecs = [m.fourcc for m in matching or self._modes if len(m.fourcc) == 4]
        codecs = sorted(set(codecs), key=lambda c: (c != 'MJPG', c != 'YUY2', c))
        if not codecs:
            codecs = ['MJPG', 'YUY2']
        codecs.append(None)
        last_error = '无法打开摄像头，请检查连接和相机权限。'
        fallbacks = []
        for codec in codecs:
            if self._stop_event.is_set():
                raise RuntimeError('已取消切换。')
            capture = None
            try:
                capture, frame = self._open_format_candidate(config, codec)
                size = (frame.shape[1], frame.shape[0])
                if size == (config.width, config.height):
                    return capture, frame
                last_error = '请求 %d × %d，设备实际返回 %d × %d。' % (config.width, config.height, *size)
                if not require_exact:
                    fallbacks.append(codec)
            except Exception as exc:
                last_error = str(exc)
            if capture is not None:
                with _DEVICE_CONTROL_LOCK:
                    capture.release()
        # Closing each graph before trying another avoids opening one device
        # twice. Reopen a fallback only after every native candidate failed.
        for codec in fallbacks:
            try:
                return self._open_format_candidate(config, codec)
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(last_error)

    def _open_format_candidate(self, config, codec):
        capture = None
        try:
            if self._stop_event.is_set():
                raise RuntimeError('已取消切换。')
            with _DEVICE_CONTROL_LOCK:
                capture = cv2.VideoCapture(int(self.device_id), cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY)
                if not capture.isOpened():
                    raise RuntimeError('无法打开摄像头，请检查连接和相机权限。')
                capture.set(cv2.CAP_PROP_FPS, config.fps)
                # DShow buffers the width until the height completes a pair.
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
                if codec:
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*codec))
            # A decoded frame, rather than the property getters, confirms size.
            for _ in range(4):
                if self._stop_event.is_set():
                    raise RuntimeError('已取消切换。')
                ok, frame = capture.read()
                if ok and frame is not None and frame.size:
                    return capture, frame
            raise RuntimeError('设备没有返回可用画面。')
        except Exception:
            if capture is not None:
                with _DEVICE_CONTROL_LOCK:
                    capture.release()
            raise

    def _accept_capture(self, capture, frame, config):
        self._capture = capture
        self._primed_frame = frame
        self._actual_height, self._actual_width = frame.shape[:2]
        reported = capture.get(cv2.CAP_PROP_FPS)
        self._device_fps = reported if math.isfinite(reported) and 1 <= reported <= 120 else config.fps
        self._frame_times.clear()
        for name, value in list(self._hardware_requests.items()):
            try:
                self._apply_hardware(name, value)
            except Exception as exc:
                self.error.emit('画面已恢复，但设备参数未能恢复：%s' % exc)

    def _apply_hardware(self, name, value):
        if name == 'zoom':
            return self._apply_zoom(value)
        labels = {"exposure": "曝光", "auto_exposure": "自动曝光", "autofocus": "自动对焦",
                  "white_balance": "白平衡", "auto_white_balance": "自动白平衡"}
        properties = {
            "exposure": cv2.CAP_PROP_EXPOSURE,
            "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
            "autofocus": cv2.CAP_PROP_AUTOFOCUS,
            # DirectShow's kelvin white-balance control uses BLUE_U in OpenCV.
            "white_balance": cv2.CAP_PROP_WHITE_BALANCE_BLUE_U,
            "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
        }
        label = labels.get(name, name)
        if self._demo or name not in properties:
            self.hardware_changed.emit(name, None, False)
            raise ValueError("%s不支持硬件控制。" % ("演示机位" if self._demo else label))
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("硬件参数必须是有限数值。")
        if name in ("auto_exposure", "autofocus", "auto_white_balance"):
            number = 1.0 if bool(value) else 0.0
        # DirectShow's auto exposure is 1/0, unlike the .75/.25 V4L convention.
        with _DEVICE_CONTROL_LOCK:
            succeeded = self._capture.set(properties[name], number)
            actual = self._capture.get(properties[name]) if succeeded else None
        if not succeeded:
            self.hardware_changed.emit(name, None, False)
            raise ValueError("设备不支持%s，或该数值超出设备范围。" % label)
        # DirectShow does not implement a getter for AUTO_EXPOSURE.
        if actual == -1 or not math.isfinite(actual):
            actual = None
        elif name == "autofocus" and os.name == "nt":
            # DShow returns CameraControl flags: Auto=1, Manual=2.
            actual = 1.0 if int(actual) & 1 else 0.0
        self._hardware[name] = actual
        self._hardware_requests[name] = value
        self.hardware_changed.emit(name, actual, True)
        self.status_changed.emit("已向设备应用%s%s" % (label, "（设备未返回数值）" if actual is None else ""))

    def _apply_zoom(self, value):
        previous = self._zoom.get('current')
        try:
            if self._demo or not self._zoom.get('supported') or self._capture is None:
                raise ValueError('此机位未报告可用的硬件变焦。')
            number = float(value)
            low, high, step = (self._zoom[k] for k in ('minimum', 'maximum', 'step'))
            if not math.isfinite(number) or number != int(number) or not low <= number <= high or (int(number) - low) % step:
                raise ValueError('变焦值须在 %d 至 %d 内，步长为 %d。' % (low, high, step))
            with _DEVICE_CONTROL_LOCK:
                success = self._capture.set(cv2.CAP_PROP_ZOOM, number)
                actual = self._capture.get(cv2.CAP_PROP_ZOOM)
            if not success or not math.isfinite(actual) or abs(actual - number) > .01:
                raise ValueError('设备未确认变焦生效，请检查设备支持情况。')
            self._zoom['current'] = int(actual)
            self._hardware['zoom'] = int(actual)
            self._hardware_requests['zoom'] = int(number)
            self.hardware_changed.emit('zoom', int(actual), True)
        except Exception:
            self.hardware_changed.emit('zoom', previous, False)
            raise

    def _emit_preview(self, frame):
        height, width = frame.shape[:2]
        if width > 960:
            frame = cv2.resize(frame, (960, max(1, round(height * 960 / width))), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], int(rgb.strides[0]), QImage.Format_RGB888).copy()
        self._preview_credit.clear()
        self.frame_ready.emit(image)

    def _emit_stats(self, now):
        measured = 0.0
        if len(self._frame_times) > 1:
            span = self._frame_times[-1] - self._frame_times[0]
            measured = (len(self._frame_times) - 1) / span if span > 0 else 0.0
        self.stats_changed.emit({
            "width": self._config.width, "height": self._config.height,
            "capture_width": self._actual_width, "capture_height": self._actual_height,
            "scaled": (self._actual_width, self._actual_height) != (self._config.width, self._config.height),
            "fps": measured, "device_fps": self._device_fps,
            "requested_width": self._config.width, "requested_height": self._config.height,
            "requested_fps": self._config.fps,
            "recording_seconds": max(0.0, now - self._record_started) if self._writer is not None else 0.0,
            "recording_remaining": max(0.0, self._record_deadline - now) if self._writer is not None and self._record_deadline is not None else None,
            "hardware": dict(self._hardware),
        })

    def _fulfil_snapshots(self):
        self._photo_jobs = [job for job in self._photo_jobs if not job.done()]
        pending, self._pending_snapshots = self._pending_snapshots, []
        frozen = None
        for request in pending:
            if len(self._photo_jobs) >= 8 or not _PHOTO_SLOTS.acquire(blocking=False):
                _release_photo_target(request)
                self.error.emit('照片正在保存，请稍候再拍。')
                continue
            try:
                if frozen is None:
                    frozen = self._frame.copy()
                self._photo_jobs.append(_PHOTO_POOL.submit(self._save_photo_job, request, frozen))
            except Exception as exc:
                _PHOTO_SLOTS.release()
                _release_photo_target(request)
                self.error.emit("保存照片失败：%s" % exc)

    def _save_photo_job(self, request, frame):
        temporary = None
        error = ''
        try:
            path = Path(request.path)
            format_for_extension('photo', path.suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and not request.overwrite:
                raise FileExistsError('照片文件已存在，请使用新文件名。')
            ok, encoded = cv2.imencode(path.suffix.lower(), frame)
            if not ok:
                raise RuntimeError('图像编码失败。')
            temporary = path.with_name('.snapshot-%s.tmp' % uuid.uuid4().hex)
            with temporary.open('xb') as stream:
                stream.write(encoded.tobytes())
            if request.overwrite:
                temporary.replace(path)
            else:
                temporary.rename(path)
        except Exception as exc:
            error = '保存照片失败：%s' % exc
        finally:
            try:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
            except OSError as exc:
                error = '\n'.join(filter(None, (error, '清理照片临时文件失败：%s' % exc)))
            finally:
                _release_photo_target(request)
                _PHOTO_SLOTS.release()
        if error:
            self.error.emit(error)
        else:
            self.snapshot_saved.emit(str(path))

    def _begin_recording(self, request):
        path = Path(request.path)
        temporary = None
        writer = None
        try:
            spec = format_for_extension('video', path.suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and not request.overwrite:
                raise FileExistsError("录像文件已存在，请使用新文件名。")
            height, width = self._frame.shape[:2]
            if width % 2 or height % 2:
                raise ValueError("录像需要偶数宽高，请先选择偶数分辨率。")
            fps = min(120.0, max(1.0, self._device_fps))
            temporary = path.with_name('.recording-%s%s' % (uuid.uuid4().hex, spec.suffix))
            writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*spec.fourcc), fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError('无法创建 %s。请检查保存目录与可用空间，或改用 AVI 格式。' % spec.label)
            self._writer = writer
            self._record_path, self._record_temp = path, temporary
            self._record_fps = fps
            self._record_started = time.perf_counter()
            self._record_deadline = self._record_started + request.duration_seconds if request.duration_seconds else None
            self._record_count = 0
            self._record_last_frame = self._frame.copy()
            self._record_last_time = self._record_started
            self._record_size = (width, height)
            self._record_overwrite = request.overwrite
            self.recording_changed.emit(True, str(path))
        except Exception as exc:
            if writer is not None:
                writer.release()
            if temporary is not None and temporary.exists():
                temporary.unlink()
            self.error.emit("开始录像失败：%s" % exc)
            self.recording_changed.emit(False, "")

    def _check_recording_backlog(self, target):
        missing = max(0, target - self._record_count)
        if missing > max(120, self._record_fps * 3):
            raise RuntimeError("录像处理已落后超过数秒，已停止以避免错误时长。请降低分辨率或帧率。")
        return missing

    def _write_recording_until(self, target):
        missing = self._check_recording_backlog(target)
        for _ in range(missing):
            self._writer.write(self._record_last_frame)
            self._record_count += 1

    def _record_frame(self, frame, now):
        try:
            if (frame.shape[1], frame.shape[0]) != self._record_size:
                raise RuntimeError("设备在录像期间改变了画面尺寸，已结束当前录像。")
            now = max(self._record_started, now)
            self._check_recording_backlog(int(math.ceil((now - self._record_started) * self._record_fps)))
            midpoint = ((self._record_last_time - self._record_started)
                        + (now - self._record_started)) / 2
            target = max(0, int(math.ceil(midpoint * self._record_fps)))
            # Assign slots before the samples' midpoint to the earlier sample.
            # Buffer the newer one until its next neighbour or recording end.
            # Tiny arrival jitter then cannot replace an unwritten sample just
            # because both arrivals fell on the same side of a clock boundary.
            self._write_recording_until(target)
            self._record_last_frame = frame.copy()
            self._record_last_time = now
        except Exception as exc:
            self.error.emit(str(exc))
            self._finish_recording(pad=False)

    def _finish_recording(self, pad=True):
        if self._writer is None:
            return
        path, temporary = self._record_path, self._record_temp
        try:
            if pad:
                stop_clock = min(time.perf_counter(), self._record_deadline) if self._record_deadline is not None else time.perf_counter()
                # Subtracting large monotonic timestamps can put an exact
                # frame boundary slightly above its integer frame count.
                target = max(1, int(math.ceil((stop_clock - self._record_started) * self._record_fps - 1e-6)))
                self._write_recording_until(target)
        except Exception as exc:
            self.error.emit("录像结束前补帧失败：%s" % exc)
        finally:
            self._writer.release()
            self._writer = None
            self._record_last_frame = None
            self._record_last_time = 0.0
        saved = False
        try:
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError("录像文件未成功写入磁盘。")
            if self._record_overwrite:
                # Keep the previous file until the new video is finalized and
                # can actually be decoded. Replacing is explicitly authorized.
                validation = cv2.VideoCapture(str(temporary), cv2.CAP_FFMPEG)
                try:
                    ok, decoded = validation.read()
                    if not ok or decoded is None or (decoded.shape[1], decoded.shape[0]) != self._record_size:
                        raise RuntimeError('新录像校验失败，未替换原文件。')
                finally:
                    validation.release()
                temporary.replace(path)
            else:
                temporary.rename(path)
            saved = True
        except Exception as exc:
            self.error.emit("录像保存失败：%s；临时文件：%s" % (exc, temporary))
        self.recording_changed.emit(False, str(path) if saved else "")
        self._record_path = self._record_temp = None
        self._record_started = 0.0
        self._record_deadline = None
        self._record_size = None
        self._record_overwrite = False

