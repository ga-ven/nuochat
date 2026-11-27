import os

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from .chat import normalize_title
from .models import ChatImage, MessageLine

_SCALED = {}


def _region(crop):
    crop = crop.astype(np.uint8)
    scale = 2 if crop.shape[0] <= 60 else 1
    if scale != 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    if float(gray.mean()) < 125:
        gray = 255 - gray
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def _bounds(box):
    try:
        points = [point for point in box if point is not None and len(point) >= 2]
        if len(points) < 2:
            return None
        return (min(float(point[0]) for point in points),
                min(float(point[1]) for point in points),
                max(float(point[0]) for point in points),
                max(float(point[1]) for point in points))
    except (TypeError, ValueError, IndexError):
        return None


def _texts(reader, crop):
    result, _ = reader(_region(crop), use_cls=False)
    texts = []
    for box, text, score in result or []:
        bounds = _bounds(box)
        if bounds and text.strip() and score >= 0.45:
            texts.append((bounds[1], text.strip(), bounds[3]))
    return texts


def _by_line(crops, padding=0):
    lines = []
    for y, text, bottom in sorted(crops, key=lambda item: (item[0], item[2])):
        if lines and abs(lines[-1][0] - y) <= padding:
            lines[-1][1].append(text)
        else:
            lines.append([y, [text]])
    return lines


def _encode_image(crop):
    height, width = crop.shape[:2]
    scale = min(1.0, 1024 / max(height, width))
    if scale < 1.0:
        crop = cv2.resize(crop, (int(width * scale), int(height * scale)),
                          interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode(
        ".jpg", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
        [int(cv2.IMWRITE_JPEG_QUALITY), 85],
    )
    if not ok:
        raise RuntimeError("聊天图片编码失败")
    return buffer.tobytes()


class WeChatOCR:
    def __init__(self, model_dir=None):
        if model_dir and os.path.isdir(model_dir):
            key = os.path.abspath(model_dir).lower()
            if key not in _SCALED:
                _SCALED[key] = RapidOCR(
                    det_model_path=os.path.join(model_dir, "ch_PP-OCRv3_det_infer.onnx"),
                    rec_model_path=os.path.join(model_dir, "ch_PP-OCRv3_rec_infer.onnx"),
                    cls_model_path=os.path.join(model_dir, "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
                    intra_op_num_threads=1, inter_op_num_threads=1,
                )
            self.reader = _SCALED[key]
        else:
            self.reader = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)

    def title(self, frame, area):
        bottom = max(1, int(area.y_top))
        crop = frame[:bottom, area.x0:area.x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        scale = 3 if crop.shape[0] <= 240 else 2
        enlarged = cv2.resize(gray, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_CUBIC)
        result, _ = self.reader(enlarged, use_cls=False)
        detected = []
        for box, text, score in result or []:
            bounds = _bounds(box)
            if bounds and text.strip() and score >= 0.45:
                detected.append((bounds[1] / scale, text.strip(), bounds[3] / scale))
        choices = ["".join(line[1]) for line in _by_line(detected, padding=8)]
        choices = [text for text in choices if normalize_title(text)]
        return max(choices, key=len) if choices else ""

    def rows(self, frame, x0, centers=None):
        height = frame.shape[0]
        offset = max(0, height // 6)
        left, right = 5, int(x0) - 4
        if centers:
            tolerance = max(36, int(x0 * 0.10))
            spans = []
            for center in sorted(int(value) for value in centers):
                top = max(offset, center - tolerance)
                bottom = min(height - 6, center + tolerance)
                if spans and top <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(spans[-1][1], bottom))
                else:
                    spans.append((top, bottom))
        else:
            spans = [(offset, height - 6)]
        rows = []
        for top, bottom in spans:
            if bottom <= top or right <= left:
                continue
            crop = frame[top:bottom, left:right]
            scale = 1.5 if centers else 1
            if scale != 1:
                crop = cv2.resize(crop, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_CUBIC)
            raw = _texts(self.reader, crop)
            adjusted = [(top + y / scale, text, top + box_bottom / scale)
                        for y, text, box_bottom in raw]
            for line in _by_line(adjusted, padding=5):
                text = "".join(line[1])
                if text and normalize_title(text):
                    rows.append((line[0], text))
        return sorted(rows, key=lambda item: item[0])

    def images(self, frame, area, text_lines=None):
        crop = frame[area.y_top:area.y_in, area.x0:area.x1]
        height, width = crop.shape[:2]
        if height < 80 or width < 120:
            return []
        background = np.asarray(area.background, dtype=np.int16)
        distance = np.abs(crop.astype(np.int16) - background).sum(axis=2)
        mask = (distance > 35).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        minimum_side = max(56, int(min(width, height) * 0.10))
        margin = max(16, int(width * 0.035))
        candidates = []
        for contour in contours:
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if (minimum_side <= box_width <= int(width * 0.55)
                    and minimum_side <= box_height <= int(height * 0.55)
                    and 0.45 <= box_width / box_height <= 3.0
                    and x >= margin and x + box_width <= width - margin
                    and y >= 8 and y + box_height <= height - 8):
                candidates.append((box_width * box_height, x, y, box_width, box_height))
        found = []
        found_images = []
        for _, x, y, box_width, box_height in sorted(candidates, reverse=True):
            if any(abs(x - other[0]) < max(12, min(box_width, other[2]) // 2)
                   and abs(y - other[1]) < max(12, min(box_height, other[3]) // 2)
                   for other in found):
                continue
            region = crop[y:y + box_height, x:x + box_width]
            gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 80, 160)
            edge_ratio = float(np.count_nonzero(edges)) / edges.size
            sample = region[::4, ::4].reshape(-1, 3)
            colors, counts = np.unique(sample, axis=0, return_counts=True)
            dominant_ratio = float(counts.max()) / len(sample)
            center_y = y + box_height / 2
            overlaps_text = any(
                abs(float(line.y) - area.y_top - center_y) <= max(12, box_height * 0.35)
                for line in text_lines or []
            )
            if edge_ratio < 0.02 and dominant_ratio > 0.55 and overlaps_text:
                continue
            kind = "in" if x + box_width / 2 < width / 2 else "out"
            found.append((x, y, box_width, box_height))
            found_images.append(ChatImage(
                kind, float(y + box_height / 2 + area.y_top), _encode_image(region),
            ))
        return sorted(found_images, key=lambda image: image.y)

    def messages(self, frame, area):
        crop = frame[area.y_top:area.y_in, area.x0:area.x1]
        detected, _ = self.reader(_region(crop), use_cls=False)
        bubble_width = max(8, int(crop.shape[1] * 0.30))
        bubbles = []
        for box, text, _ in detected or []:
            text = text.strip()
            bounds = _bounds(box)
            if not bounds or not text or bounds[2] - bounds[0] < bubble_width:
                continue
            left, top, _, bottom = (int(value) for value in bounds)
            center_x = area.x0 + int(crop.shape[1] * 0.45)
            kind = "in" if left < center_x else "out"
            bubbles.append((int((top + bottom) / 2), left, bottom - top, text, kind))
        if not bubbles:
            return []
        median_height = float(np.median([item[2] for item in bubbles]))
        groups = []
        for bubble in sorted(bubbles, key=lambda item: item[0]):
            if (groups and bubble[4] == groups[-1][-1][4]
                    and bubble[0] - groups[-1][-1][0] <= max(6, median_height * 1.15)):
                groups[-1].append(bubble)
            else:
                groups.append([bubble])
        lines = []
        for group in groups:
            members = sorted(group, key=lambda item: item[1])
            lines.append(MessageLine(
                members[0][4], " ".join(item[3] for item in members),
                float(members[0][0] + area.y_top),
            ))
        return lines
