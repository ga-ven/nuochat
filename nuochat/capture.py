import ctypes
import os
import time
from ctypes import wintypes

import cv2
import numpy as np

from .models import ChatArea, WeChatInstance

u32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
k32 = ctypes.windll.kernel32


class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
        ("image_size", wintypes.DWORD), ("x_ppm", wintypes.LONG), ("y_ppm", wintypes.LONG),
        ("colors_used", wintypes.DWORD), ("colors_important", wintypes.DWORD),
    ]


def _process_identity(pid):
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return "", 0
    try:
        name = ctypes.create_unicode_buffer(1024)
        length = wintypes.DWORD(1024)
        ok = k32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length))
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        timed = k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user))
        stamp = ((created.dwHighDateTime << 32) | created.dwLowDateTime) if timed else 0
        return (os.path.basename(name.value).lower() if ok else ""), stamp
    finally:
        k32.CloseHandle(handle)


def list_instances():
    groups = {}
    cache = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in cache:
            cache[pid.value] = _process_identity(pid.value)
        executable, started = cache[pid.value]
        if executable not in {"weixin.exe", "wechat.exe"} or not started:
            return True
        title = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, title, 256)
        rect = wintypes.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 200 and height < 200 and not u32.IsIconic(hwnd):
            return True
        key = (pid.value, started)
        groups.setdefault(key, []).append(WeChatInstance(
            hwnd=hwnd, pid=pid.value, started=started, width=width, height=height,
            left=rect.left, top=rect.top, title=title.value or "微信",
        ))
        return True

    u32.EnumWindows(visit, 0)
    instances = []
    for records in groups.values():
        main = [item for item in records if item.title == "微信"]
        chosen = max(main or records, key=lambda item: item.width * item.height)
        instances.append(chosen)
    return sorted(instances, key=lambda item: item.pid)


def resolve_instance(instance_id):
    for instance in list_instances():
        if instance.instance_id == instance_id:
            return instance
    raise RuntimeError("选中的聊天窗口已关闭或重启，请重新选择")


def restore_without_activation(hwnd):
    if not u32.IsIconic(hwnd):
        return False
    u32.ShowWindow(hwnd, 4)
    u32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x13)
    return True


def grab(hwnd):
    rect = wintypes.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None, None
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 200 or height < 200:
        return None, None
    window_dc = u32.GetWindowDC(hwnd)
    if not window_dc:
        return None, None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        if not memory_dc or not bitmap:
            return None, None
        gdi32.SelectObject(memory_dc, bitmap)
        if not u32.PrintWindow(hwnd, memory_dc, 2):
            return None, None
        header = _BitmapHeader()
        header.size = ctypes.sizeof(header)
        header.width, header.height = width, -height
        header.planes, header.bits, header.compression = 1, 32, 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        if copied != height:
            return None, None
        bgra = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)
        rgb = np.ascontiguousarray(bgra[:, :, 2::-1])
        return rgb, (rect.left, rect.top, width, height)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        u32.ReleaseDC(hwnd, window_dc)


def locate_chat_area(frame):
    if frame is None:
        return None
    height, width = frame.shape[:2]
    sample = frame[::8, width // 2::8].reshape(-1, 3)
    colors, counts = np.unique(sample, axis=0, return_counts=True)
    background = tuple(int(value) for value in colors[counts.argmax()])
    mask = np.abs(frame.astype(np.int16) - np.asarray(background)).sum(axis=2) <= 8
    header_top, header_bottom = max(20, height // 10), min(height // 5, height - 20)
    columns = np.maximum(
        mask[header_top:header_bottom].mean(axis=0),
        mask[height // 4:height * 3 // 4].mean(axis=0),
    )
    column_match = columns > 0.30
    edges = np.diff(np.r_[False, column_match, False].astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    minimum_left = max(20, int(width * 0.12))
    # 深色主题左侧图标栏可能与聊天区同色，取最长列段并合并窄内容造成的近邻断点。
    runs = [(int(end - start), int(start), int(end))
            for start, end in zip(starts, ends)
            if start >= minimum_left and end - start >= 4]
    if not runs:
        return None
    merged = []
    maximum_gap = max(12, int(width * 0.03))
    for length, start, end in runs:
        if merged and start - merged[-1][2] <= maximum_gap:
            previous_length, previous_start, previous_end = merged[-1]
            merged[-1] = (previous_length + length + start - previous_end,
                          previous_start, end)
        else:
            merged.append((length, start, end))
    merged = [run for run in merged if run[0] >= 120]
    if not merged:
        return None
    _, left, right = max(merged)
    rows = mask[:, left:right].mean(axis=1)
    pane_top = int(np.argmax(rows > 0.90))
    pane_bottom = height - int(np.argmax(rows[::-1] > 0.90))
    if (right - left < 120 or pane_bottom - pane_top < 160
            or left < max(20, int(width * 0.12)) or sum(background) < 10):
        return None
    panel = frame[pane_top:pane_bottom, left:right].astype(np.int16)
    separators = pane_top + np.where(
        (panel.std(axis=(1, 2)) < 4) & (rows[pane_top:pane_bottom] < 0.10)
    )[0]
    separated = [int(value) for index, value in enumerate(separators)
                 if index == 0 or value - separators[index - 1] > 3]
    input_candidates = [value for value in separated
                        if value > pane_top + (pane_bottom - pane_top) * 0.45]
    input_top = input_candidates[0] if input_candidates else pane_bottom
    header_candidates = [value for value in separated
                         if pane_top + 55 < value < input_top - 50]
    chat_top = header_candidates[-1] if header_candidates else pane_top + 60
    if input_top - chat_top < 50:
        return None
    return ChatArea(left, chat_top, right, input_top, background, pane_top)


def _red_mask(region):
    red = region[:, :, 0].astype(np.int16)
    green = region[:, :, 1].astype(np.int16)
    blue = region[:, :, 2].astype(np.int16)
    return ((red > 190) & (green < 135) & (blue < 135)
            & (red - green > 80) & (red - blue > 80)).astype(np.uint8)


def find_unread_rows(frame, x0, band=170):
    if frame is None or x0 <= 4:
        return []
    height, width = frame.shape[:2]
    x0 = int(x0)
    mask = np.zeros((height, width), dtype=np.uint8)
    ranges = (
        (max(0, x0 - band), min(width, x0 - 4)),
        (max(0, int(x0 * 0.20)), min(width, int(x0 * 0.43))),
    )
    for start, end in ranges:
        if end > start:
            mask[:, start:end] = _red_mask(frame[:, start:end])
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    minimum_side = max(4, int(x0 * 0.012))
    maximum_side = max(minimum_side + 1, int(x0 * 0.09))
    minimum_area = max(4, int((x0 * 0.018) ** 2 * 0.30))
    centers = []
    for index in range(1, count):
        left, top, box_width, box_height, area = stats[index]
        if (minimum_area <= area <= maximum_side * maximum_side * 4
                and minimum_side <= box_width <= maximum_side
                and minimum_side <= box_height <= min(maximum_side, height // 6)
                and box_width * 2 >= box_height and box_height * 2 >= box_width):
            centers.append(int(top + box_height // 2))
    centers.sort()
    unique = []
    for center in centers:
        if not unique or center - unique[-1] > 6:
            unique.append(center)
    return unique


def wait_for_frame(hwnd, attempts=3, delay=0.12):
    if restore_without_activation(hwnd):
        time.sleep(delay)
    for _ in range(attempts):
        frame, rect = grab(hwnd)
        if frame is not None:
            return frame, rect
        time.sleep(delay)
    return None, None
