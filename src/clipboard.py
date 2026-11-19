"""Windows のクリップボード (Unicode テキスト) を、追加のライブラリなしで読み書きする。"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _api():
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.restype = wintypes.BOOL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = wintypes.LPVOID
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    return u32, k32


def _open(u32, tries: int = 20) -> None:
    for _ in range(tries):  # ほかのアプリが開いていると失敗するので、少し待って何度か試す
        if u32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("クリップボードを開けませんでした")


def get_text() -> str:
    if sys.platform != "win32":
        raise OSError("Windows 専用です")
    u32, k32 = _api()
    _open(u32)
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def set_text(text: str) -> None:
    if sys.platform != "win32":
        raise OSError("Windows 専用です")
    u32, k32 = _api()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, size)
    k32.GlobalUnlock(h)
    _open(u32)
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(CF_UNICODETEXT, h):
            raise OSError("クリップボードに書けませんでした")
    finally:
        u32.CloseClipboard()
