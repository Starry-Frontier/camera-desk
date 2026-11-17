"""Enumerate DirectShow capture devices without opening their video streams.

Only the standard Windows COM APIs are used.  Enumeration order is deliberately
preserved because OpenCV's CAP_DSHOW backend uses that order for camera indexes.
Each call owns and releases its COM objects on the calling thread.

References:
https://learn.microsoft.com/windows/win32/directshow/selecting-a-capture-device
https://learn.microsoft.com/windows/win32/api/objidl/nn-objidl-imoniker
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str
    device_id: str


class CameraDiscoveryError(RuntimeError):
    """The Windows device enumerator could not complete its request."""


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _VariantValue(ctypes.Union):
    _fields_ = [
        ("llVal", ctypes.c_int64),
        ("bstrVal", ctypes.c_void_p),
        # The BRECORD member sets VARIANT's architecture-dependent union size.
        ("record", ctypes.c_void_p * 2),
    ]


class _VARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_uint16),
        ("wReserved1", ctypes.c_uint16),
        ("wReserved2", ctypes.c_uint16),
        ("wReserved3", ctypes.c_uint16),
        ("value", _VariantValue),
    ]


def _method(pointer, slot, result_type, *arg_types):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    address = vtable.contents[slot]
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *arg_types)(address)


def _release(pointer):
    if pointer:
        _method(pointer, 2, ctypes.c_uint32)(pointer)


def _check(hr, action):
    if hr < 0:
        raise CameraDiscoveryError(
            "{}失败（Windows 错误 0x{:08X}）。".format(action, hr & 0xFFFFFFFF)
        )


def _read_string(bag, name, oleaut32):
    if not bag:
        return ""
    variant = _VARIANT()
    try:
        result = _method(
            bag, 3, ctypes.c_long, ctypes.c_wchar_p,
            ctypes.POINTER(_VARIANT), ctypes.c_void_p,
        )(bag, name, ctypes.byref(variant), None)
        if result >= 0 and variant.vt == 8 and variant.value.bstrVal:
            return ctypes.wstring_at(variant.value.bstrVal).strip()
        return ""
    finally:
        oleaut32.VariantClear(ctypes.byref(variant))


def _display_name(moniker, bind_context, ole32):
    name = ctypes.c_void_p()
    try:
        result = _method(
            moniker, 20, ctypes.c_long, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        )(moniker, bind_context, None, ctypes.byref(name))
        if result >= 0 and name:
            return ctypes.wstring_at(name.value)
        return ""
    finally:
        if name:
            ole32.CoTaskMemFree(name)


def _open_property_bag(moniker):
    bag = ctypes.c_void_p()
    iid = _GUID.parse('55272A00-42CB-11CE-8135-00AA004BB851')
    result = _method(moniker, 9, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                     ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                         moniker, None, None, ctypes.byref(iid), ctypes.byref(bag))
    if result < 0:
        _release(bag)
        return ctypes.c_void_p()
    return bag


def _device_identity(moniker, bag, bind_context, ole32, oleaut32):
    return _read_string(bag, 'DevicePath', oleaut32) or _display_name(moniker, bind_context, ole32)


def _variant_library():
    oleaut32 = ctypes.WinDLL('oleaut32')
    oleaut32.VariantClear.argtypes = [ctypes.POINTER(_VARIANT)]
    oleaut32.VariantClear.restype = ctypes.c_long
    return oleaut32


def _camera_bind_context(ole32):
    ole32.CreateBindCtx.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    ole32.CreateBindCtx.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    bind_context = ctypes.c_void_p()
    # Display names are a fallback only, so a bind-context error is nonfatal.
    ole32.CreateBindCtx(0, ctypes.byref(bind_context))
    return bind_context


def _bind_camera_filter(ole32, enumerator, device):
    """Bind the identified moniker from this enumeration directly to a filter."""
    by_identity = isinstance(device, str)
    oleaut32 = _variant_library() if by_identity else None
    bind_context = _camera_bind_context(ole32) if by_identity else ctypes.c_void_p()
    index = 0
    try:
        while True:
            moniker, fetched = ctypes.c_void_p(), ctypes.c_uint32()
            result = _method(enumerator, 3, ctypes.c_long, ctypes.c_uint32,
                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32))(
                                 enumerator, 1, ctypes.byref(moniker), ctypes.byref(fetched))
            try:
                _check(result, '查找摄像头')
                if result == 1 or not moniker:
                    return ctypes.c_void_p()
                if by_identity:
                    bag = _open_property_bag(moniker)
                    try:
                        candidate = _device_identity(moniker, bag, bind_context, ole32, oleaut32)
                    finally:
                        _release(bag)
                else:
                    candidate = index
                if candidate == device:
                    filter_ = ctypes.c_void_p()
                    iid = _GUID.parse('56A86895-0AD4-11CE-B03A-0020AF0BA770')
                    result = _method(moniker, 8, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                                         moniker, None, None, ctypes.byref(iid), ctypes.byref(filter_))
                    if result < 0:
                        _release(filter_)
                        _check(result, '打开所选摄像头')
                    return filter_
                index += 1
            finally:
                _release(moniker)
    finally:
        _release(bind_context)


def enumerate_cameras() -> List[CameraDevice]:
    """Return devices in DirectShow index order; return [] on other platforms.

    DevicePath is a stable identity for saved settings while a camera remains
    connected to its USB port.  Moniker display name is used if DevicePath is
    unavailable.  No capture device is activated and no camera permission prompt
    is triggered by this enumeration.
    """
    if sys.platform != "win32":
        return []

    # WinDLL keeps raw HRESULT values so S_FALSE / RPC_E_CHANGED_MODE can be
    # handled explicitly instead of OleDLL translating them into exceptions.
    ole32 = ctypes.WinDLL("ole32")
    oleaut32 = _variant_library()
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long

    clsid_enum = _GUID.parse("62BE5D10-60EB-11D0-BD3B-00A0C911CE86")
    iid_enum = _GUID.parse("29840822-5B84-11D0-BD3B-00A0C911CE86")
    clsid_video = _GUID.parse("860BB310-5D01-11D0-BD3B-00A0C911CE86")

    # CoInitializeEx may report an existing STA; COM is already usable then.
    # S_OK and S_FALSE both acquire a reference that must be uninitialized.
    init_hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
    initialized_here = init_hr >= 0
    if init_hr < 0 and (init_hr & 0xFFFFFFFF) != 0x80010106:  # RPC_E_CHANGED_MODE
        _check(init_hr, "初始化摄像头列表")

    creator = ctypes.c_void_p()
    enumerator = ctypes.c_void_p()
    bind_context = ctypes.c_void_p()
    try:
        _check(ole32.CoCreateInstance(
            ctypes.byref(clsid_enum), None, 1, ctypes.byref(iid_enum),
            ctypes.byref(creator),
        ), "读取摄像头列表")
        result = _method(
            creator, 3, ctypes.c_long, ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
        )(creator, ctypes.byref(clsid_video), ctypes.byref(enumerator), 0)
        _check(result, "枚举摄像头")
        if result == 1 or not enumerator:  # S_FALSE: no capture devices
            return []
        bind_context = _camera_bind_context(ole32)
        devices = []
        while True:
            moniker = ctypes.c_void_p()
            fetched = ctypes.c_uint32()
            result = _method(
                enumerator, 3, ctypes.c_long, ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
            )(enumerator, 1, ctypes.byref(moniker), ctypes.byref(fetched))
            _check(result, "读取摄像头信息")
            if result == 1 or fetched.value == 0:
                break
            bag = ctypes.c_void_p()
            try:
                bag = _open_property_bag(moniker)
                index = len(devices)
                name = _read_string(bag, "FriendlyName", oleaut32)
                if not name and bag:
                    name = _read_string(bag, "Description", oleaut32)
                device_id = _device_identity(moniker, bag, bind_context, ole32, oleaut32)
                devices.append(CameraDevice(
                    index=index,
                    name=name or "摄像头 {}".format(index + 1),
                    device_id=device_id or "dshow:index:{}".format(index),
                ))
            finally:
                _release(bag)
                _release(moniker)
        return devices
    except OSError as exc:
        raise CameraDiscoveryError("无法读取 Windows 摄像头列表：{}".format(exc)) from exc
    finally:
        _release(bind_context)
        _release(enumerator)
        _release(creator)
        if initialized_here:
            ole32.CoUninitialize()
