import ctypes
import time
from ctypes import wintypes

import cv2
import numpy as np

from .capture import grab, resolve_instance, restore_without_activation

u32 = ctypes.windll.user32

u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.PostMessageW.restype = wintypes.BOOL
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.SendMessageW.restype = wintypes.LPARAM
u32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
u32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]

_PER_MONITOR_AWARE_V2 = wintypes.HANDLE(-4)


def _post_message(hwnd, message, flags, packed):
    if not u32.PostMessageW(hwnd, message, flags, packed):
        raise RuntimeError("聊天窗口拒绝了后台操作")


def click_row(instance_id, y):
    previous = u32.SetThreadDpiAwarenessContext(_PER_MONITOR_AWARE_V2)
    try:
        instance = resolve_instance(instance_id)
        point = wintypes.POINT(instance.left + int(instance.width * 0.18),
                               instance.top + int(y))
        if not u32.ScreenToClient(instance.hwnd, ctypes.byref(point)):
            raise RuntimeError("无法定位聊天窗口会话行")
        packed = (int(point.y) << 16) | (int(point.x) & 0xFFFF)
        for message, flags in ((0x0201, 1), (0x0202, 0)):
            _post_message(instance.hwnd, message, flags, packed)
    finally:
        u32.SetThreadDpiAwarenessContext(previous)
    time.sleep(0.14)


def _input_position(instance, area):
    height = instance.height
    if 0 < area.y_in <= height and area.y_in < height - 20:
        return instance.width // 2, area.y_in + min(42, max(24, (height - area.y_in) // 2))
    return int(instance.width * 0.67), max(40, height - 76)


def _click_client(instance, x, y):
    packed = (int(y) << 16) | (int(x) & 0xFFFF)
    for message, flags in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
        _post_message(instance.hwnd, message, flags, packed)
        time.sleep(0.02)


def _click_client_sync(instance, x, y):
    packed = (int(y) << 16) | (int(x) & 0xFFFF)
    for message, flags in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
        u32.SendMessageW(instance.hwnd, message, flags, packed)
        time.sleep(0.03)


def _window_key(hwnd, vk, key_up=False):
    scan = u32.MapVirtualKeyW(vk, 0)
    flags = 2 if key_up else 0
    u32.SendMessageW(hwnd, 0x101 if key_up else 0x100, vk, (scan << 16) | flags | 1)


def _press_ctrl_end(hwnd):
    _window_key(hwnd, 0x11)
    _window_key(hwnd, 0x23)
    _window_key(hwnd, 0x23, key_up=True)
    _window_key(hwnd, 0x11, key_up=True)


def _type_text(hwnd, text):
    # 后台窗口不响应全局粘贴，同步投递字符可保留用户当前前台窗口。
    for character in text:
        code = ord(character)
        units = [code]
        if code > 0xFFFF:
            code -= 0x10000
            units = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
        for unit in units:
            u32.SendMessageW(hwnd, 0x102, unit, 1)
        time.sleep(0.01)


def fill_reply(instance_id, area, text, at_target=None):
    instance = resolve_instance(instance_id)
    if restore_without_activation(instance.hwnd):
        time.sleep(0.2)
        instance = resolve_instance(instance_id)
    relative_x, relative_y = _input_position(instance, area)
    _click_client(instance, relative_x, relative_y)
    time.sleep(0.1)
    _press_ctrl_end(instance.hwnd)
    if at_target:
        _type_text(instance.hwnd, "@" + at_target)
        time.sleep(0.35)
        _window_key(instance.hwnd, 0x0D)
        time.sleep(0.20)
    _type_text(instance.hwnd, text)


def _send_button_position(frame, area):
    height, width = frame.shape[:2]
    top = max(area.y_in, int(height * 0.55))
    region = frame[top:height, area.x0:width].astype(np.int16)
    red, green, blue = region[:, :, 0], region[:, :, 1], region[:, :, 2]
    mask = ((green > 120) & (green - red > 70) & (green - blue > 35)
            & (red < 120)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    candidates = []
    for x, y, box_width, box_height, size in stats[1:]:
        center_x = area.x0 + int(x + box_width // 2)
        center_y = top + int(y + box_height // 2)
        if (200 <= size <= 5000 and 20 <= box_width <= 90 and 18 <= box_height <= 60
                and center_x > area.x0 + (width - area.x0) * 0.55
                and center_y > area.y_in + (height - area.y_in) * 0.45):
            candidates.append((int(size), center_x, center_y))
    if not candidates:
        raise RuntimeError("未找到可用的发送按钮，请检查输入内容后手动发送")
    _, x, y = max(candidates)
    return x, y


def send_filled_reply(instance_id, area):
    instance = resolve_instance(instance_id)
    if restore_without_activation(instance.hwnd):
        time.sleep(0.2)
        instance = resolve_instance(instance_id)
    time.sleep(0.2)
    frame, _ = grab(instance.hwnd)
    if frame is None:
        raise RuntimeError("发送前画面抓取失败，请手动确认后发送")
    x, y = _send_button_position(frame, area)
    _click_client_sync(instance, x, y)
