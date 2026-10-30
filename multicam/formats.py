"""Read each DirectShow camera's advertised output modes via IAMStreamConfig.

No stream is started here. All COM objects and media-type buffers are released
on the calling camera worker thread.
https://learn.microsoft.com/windows/win32/directshow/configure-the-video-output-format
"""
from __future__ import annotations

import ctypes as C
import sys
from dataclasses import dataclass

from .devices import _GUID, _check, _method, _release


@dataclass(frozen=True)
class CameraMode:
    width: int
    height: int
    max_fps: float
    fourcc: str = ''


class _MediaType(C.Structure):
    _fields_ = [('major', _GUID), ('subtype', _GUID), ('fixed', C.c_int32),
                ('temporal', C.c_int32), ('sample_size', C.c_uint32),
                ('format_type', _GUID), ('unknown', C.c_void_p),
                ('format_size', C.c_uint32), ('format_data', C.c_void_p)]


class _Size(C.Structure):
    _fields_ = [('width', C.c_int32), ('height', C.c_int32)]


class _StreamCaps(C.Structure):
    _fields_ = [('guid', _GUID), ('standard', C.c_uint32), ('input', _Size),
                ('min_crop', _Size), ('max_crop', _Size),
                ('crop_x', C.c_int32), ('crop_y', C.c_int32),
                ('align_x', C.c_int32), ('align_y', C.c_int32),
                ('min_output', _Size), ('max_output', _Size),
                ('output_x', C.c_int32), ('output_y', C.c_int32),
                ('stretch_x', C.c_int32), ('stretch_y', C.c_int32),
                ('shrink_x', C.c_int32), ('shrink_y', C.c_int32),
                ('min_interval', C.c_int64), ('max_interval', C.c_int64),
                ('min_bits', C.c_int32), ('max_bits', C.c_int32)]


def _guid_equal(left, value):
    return bytes(left) == bytes(_GUID.parse(value))


def _parse_mode(media, caps):
    import struct
    if not _guid_equal(media.major, '73646976-0000-0010-8000-00AA00389B71'):
        return None
    if _guid_equal(media.format_type, '05589F80-C356-11CE-BF01-00AA0055595A'):
        bitmap_offset = 48  # VIDEOINFOHEADER
    elif _guid_equal(media.format_type, 'F72A76A0-EB0A-11D0-ACE4-0000C0CC16BA'):
        bitmap_offset = 72  # VIDEOINFOHEADER2
    else:
        return None
    if not media.format_data or media.format_size < bitmap_offset + 40:
        return None
    data = C.string_at(media.format_data, media.format_size)
    width, height = struct.unpack_from('<ii', data, bitmap_offset + 4)
    width, height = abs(width), abs(height)
    if not (16 <= width <= 7680 and 16 <= height <= 4320):
        return None
    interval = caps.min_interval or struct.unpack_from('<q', data, 40)[0]
    fps = round(10000000 / interval, 3) if interval > 0 else 30.0
    subtype = int(media.subtype.Data1).to_bytes(4, 'little')
    codec = subtype.decode('ascii') if all(32 <= ch <= 126 for ch in subtype) else ''
    return CameraMode(width, height, fps, codec)


def _pin_modes(pin, ole):
    config = C.c_void_p()
    iid = _GUID.parse('C6E13340-30AC-11D0-A18C-00A0C9118956')
    result = _method(pin, 0, C.c_long, C.POINTER(_GUID), C.POINTER(C.c_void_p))(
        pin, C.byref(iid), C.byref(config))
    if result < 0 or not config:
        return []
    try:
        count, size = C.c_int(), C.c_int()
        result = _method(config, 5, C.c_long, C.POINTER(C.c_int), C.POINTER(C.c_int))(
            config, C.byref(count), C.byref(size))
        if result < 0 or not (0 < count.value <= 4096 and C.sizeof(_StreamCaps) <= size.value <= 65536):
            return []
        modes = []
        for index in range(count.value):
            buffer = C.create_string_buffer(size.value)
            media = C.POINTER(_MediaType)()
            try:
                result = _method(config, 6, C.c_long, C.c_int,
                                 C.POINTER(C.POINTER(_MediaType)), C.c_void_p)(
                                     config, index, C.byref(media), buffer)
                if result >= 0 and media:
                    mode = _parse_mode(media.contents, _StreamCaps.from_buffer_copy(buffer))
                    if mode:
                        modes.append(mode)
            finally:
                if media:
                    if media.contents.format_data:
                        ole.CoTaskMemFree(media.contents.format_data)
                    _release(media.contents.unknown)
                    ole.CoTaskMemFree(C.cast(media, C.c_void_p))
        return modes
    finally:
        _release(config)


def enumerate_camera_modes(device_index):
    if sys.platform != 'win32':
        return []
    ole = C.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint32]
    ole.CoInitializeEx.restype = C.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoTaskMemFree.argtypes = [C.c_void_p]
    ole.CoCreateInstance.argtypes = [C.POINTER(_GUID), C.c_void_p, C.c_uint32,
                                   C.POINTER(_GUID), C.POINTER(C.c_void_p)]
    ole.CoCreateInstance.restype = C.c_long
    initialized = ole.CoInitializeEx(None, 2)  # STA, same as OpenCV DirectShow
    if initialized < 0 and (initialized & 0xFFFFFFFF) != 0x80010106:
        _check(initialized, '读取摄像头支持格式')
    creator, enum, filter_, pins = (C.c_void_p() for _ in range(4))
    try:
        clsid = _GUID.parse('62BE5D10-60EB-11D0-BD3B-00A0C911CE86')
        iid = _GUID.parse('29840822-5B84-11D0-BD3B-00A0C911CE86')
        category = _GUID.parse('860BB310-5D01-11D0-BD3B-00A0C911CE86')
        _check(ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(creator)), '访问摄像头')
        result = _method(creator, 3, C.c_long, C.POINTER(_GUID), C.POINTER(C.c_void_p), C.c_uint32)(
            creator, C.byref(category), C.byref(enum), 0)
        _check(result, '读取摄像头')
        if result == 1 or not enum:
            return []
        for index in range(int(device_index) + 1):
            moniker, fetched = C.c_void_p(), C.c_uint32()
            result = _method(enum, 3, C.c_long, C.c_uint32, C.POINTER(C.c_void_p), C.POINTER(C.c_uint32))(
                enum, 1, C.byref(moniker), C.byref(fetched))
            _check(result, '查找摄像头')
            if result == 1 or not moniker:
                return []
            try:
                if index == int(device_index):
                    iid_filter = _GUID.parse('56A86895-0AD4-11CE-B03A-0020AF0BA770')
                    _check(_method(moniker, 8, C.c_long, C.c_void_p, C.c_void_p,
                                   C.POINTER(_GUID), C.POINTER(C.c_void_p))(
                                       moniker, None, None, C.byref(iid_filter), C.byref(filter_)), '读取设备格式')
            finally:
                _release(moniker)
        _check(_method(filter_, 10, C.c_long, C.POINTER(C.c_void_p))(filter_, C.byref(pins)), '读取视频输出')
        modes = []
        while True:
            pin, fetched = C.c_void_p(), C.c_uint32()
            result = _method(pins, 3, C.c_long, C.c_uint32, C.POINTER(C.c_void_p), C.POINTER(C.c_uint32))(
                pins, 1, C.byref(pin), C.byref(fetched))
            _check(result, '读取输出格式')
            if result == 1 or not pin:
                break
            try:
                direction = C.c_int()
                if _method(pin, 9, C.c_long, C.POINTER(C.c_int))(pin, C.byref(direction)) >= 0 and direction.value == 1:
                    modes.extend(_pin_modes(pin, ole))
            finally:
                _release(pin)
        return sorted(set(modes), key=lambda m: (m.width * m.height, m.width, m.height, m.fourcc, m.max_fps))
    finally:
        for pointer in (pins, filter_, enum, creator):
            _release(pointer)
        if initialized >= 0:
            ole.CoUninitialize()
