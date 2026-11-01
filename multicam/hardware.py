"""Read DirectShow zoom support without moving the lens or starting a stream.

https://learn.microsoft.com/windows/win32/api/strmif/nf-strmif-iamcameracontrol-getrange
"""
import ctypes as C
import sys

from .devices import _GUID, _check, _method, _release


def read_camera_zoom(device_index):
    unavailable = {'supported': False, 'reason': '设备未报告可用的手动变焦范围'}
    if sys.platform != 'win32':
        return unavailable
    ole = C.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [C.c_void_p, C.c_uint32]
    ole.CoInitializeEx.restype = C.c_long
    ole.CoCreateInstance.argtypes = [C.POINTER(_GUID), C.c_void_p, C.c_uint32,
                                   C.POINTER(_GUID), C.POINTER(C.c_void_p)]
    ole.CoCreateInstance.restype = C.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and (initialized & 0xffffffff) != 0x80010106:
        _check(initialized, '读取变焦范围')
    creator, enum, filter_, control = (C.c_void_p() for _ in range(4))
    try:
        clsid = _GUID.parse('62BE5D10-60EB-11D0-BD3B-00A0C911CE86')
        iid = _GUID.parse('29840822-5B84-11D0-BD3B-00A0C911CE86')
        category = _GUID.parse('860BB310-5D01-11D0-BD3B-00A0C911CE86')
        _check(ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(creator)), '读取变焦设备')
        result = _method(creator, 3, C.c_long, C.POINTER(_GUID), C.POINTER(C.c_void_p), C.c_uint32)(
            creator, C.byref(category), C.byref(enum), 0)
        _check(result, '读取变焦设备')
        if result == 1 or not enum:
            return unavailable
        for index in range(int(device_index) + 1):
            moniker, fetched = C.c_void_p(), C.c_uint32()
            result = _method(enum, 3, C.c_long, C.c_uint32, C.POINTER(C.c_void_p), C.POINTER(C.c_uint32))(
                enum, 1, C.byref(moniker), C.byref(fetched))
            if result != 0 or not moniker:
                return unavailable
            try:
                if index == int(device_index):
                    iid_filter = _GUID.parse('56A86895-0AD4-11CE-B03A-0020AF0BA770')
                    _check(_method(moniker, 8, C.c_long, C.c_void_p, C.c_void_p,
                                   C.POINTER(_GUID), C.POINTER(C.c_void_p))(
                                       moniker, None, None, C.byref(iid_filter), C.byref(filter_)), '读取变焦接口')
            finally:
                _release(moniker)
        iid_control = _GUID.parse('C6E13370-30AC-11D0-A18C-00A0C9118956')
        result = _method(filter_, 0, C.c_long, C.POINTER(_GUID), C.POINTER(C.c_void_p))(
            filter_, C.byref(iid_control), C.byref(control))
        if result < 0 or not control:
            return unavailable
        low, high, step, default, flags = (C.c_long() for _ in range(5))
        result = _method(control, 3, C.c_long, C.c_long, *([C.POINTER(C.c_long)] * 5))(
            control, 3, C.byref(low), C.byref(high), C.byref(step), C.byref(default), C.byref(flags))
        if result < 0 or not flags.value & 2 or high.value <= low.value or step.value <= 0:
            return unavailable
        current, mode = C.c_long(), C.c_long()
        result = _method(control, 5, C.c_long, C.c_long, C.POINTER(C.c_long), C.POINTER(C.c_long))(
            control, 3, C.byref(current), C.byref(mode))
        return {'supported': True, 'minimum': low.value, 'maximum': high.value,
                'step': step.value, 'default': default.value,
                'current': current.value if result >= 0 else None}
    finally:
        for pointer in (control, filter_, enum, creator):
            _release(pointer)
        if initialized >= 0:
            ole.CoUninitialize()
