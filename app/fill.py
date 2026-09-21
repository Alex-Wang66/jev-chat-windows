# -*- coding: utf-8 -*-
"""把选中的候选填进微信输入框：写剪贴板 → 点输入框 → Ctrl+V。绝不发回车、绝不点发送。"""
import ctypes
import ctypes.wintypes as w

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32


def set_clipboard(text):
    data = text.encode("utf-16-le") + b"\0\0"
    h = k32.GlobalAlloc(0x2, len(data))  # GMEM_MOVEABLE
    ctypes.memmove(k32.GlobalLock(h), data, len(data))
    k32.GlobalUnlock(h)
    u32.OpenClipboard(None)
    u32.EmptyClipboard()
    u32.SetClipboardData(13, h)  # CF_UNICODETEXT
    u32.CloseClipboard()


def fill(hwnd, area, text):
    """area = 消息区 (x0, y0, x1, y1)；输入框就在底线 y1 下面。"""
    from app.capture import unminimize

    set_clipboard(text)
    r = w.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # 扩展边界，跟 WGC 帧对齐
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    x0, _, _, y1 = area
    cx, cy = r.left + x0 + 60, r.top + y1 + 40  # 分隔线下 40px = 输入框文字区；工具栏和「发送」在输入区最底下，碰不到
    unminimize(hwnd)
    u32.SetForegroundWindow(hwnd)
    old = w.POINT()
    u32.GetCursorPos(ctypes.byref(old))
    u32.SetCursorPos(cx, cy)
    u32.mouse_event(0x2, 0, 0, 0, 0)  # 左键按下
    u32.mouse_event(0x4, 0, 0, 0, 0)  # 抬起
    u32.SetCursorPos(old.x, old.y)
    u32.keybd_event(0x11, 0, 0, 0)  # Ctrl
    u32.keybd_event(0x56, 0, 0, 0)  # V
    u32.keybd_event(0x56, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)
    # 到此为止。发不发、改不改，人来。
