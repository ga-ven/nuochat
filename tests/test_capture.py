import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from nuochat import automation
from nuochat.capture import find_unread_rows, locate_chat_area
from nuochat.models import ChatArea, WeChatInstance
from nuochat.ocr import WeChatOCR


class CaptureTests(unittest.TestCase):
    def test_instance_identity(self):
        first = WeChatInstance(1, 100, 200, 1000, 700)
        second = WeChatInstance(2, 100, 201, 1000, 700)
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(first.instance_id, "wechat:100:200")

    def test_unread_components(self):
        frame = np.full((700, 1000, 3), 30, dtype=np.uint8)
        cv2.circle(frame, (260, 220), 9, (240, 45, 50), -1)
        cv2.circle(frame, (90, 460), 7, (240, 45, 50), -1)
        cv2.circle(frame, (600, 220), 30, (240, 45, 50), -1)
        rows = find_unread_rows(frame, 300)
        self.assertTrue(any(abs(row - 220) <= 2 for row in rows))
        self.assertTrue(any(abs(row - 460) <= 2 for row in rows))
        self.assertFalse(any(abs(row - 220) <= 2 and row != 220 for row in rows))

    def test_unread_components_at_narrow_window_scale(self):
        frame = np.full((647, 894, 3), 250, dtype=np.uint8)
        cv2.rectangle(frame, (193, 271), (207, 284), (240, 45, 50), -1)
        cv2.rectangle(frame, (148, 551), (189, 563), (240, 45, 50), -1)
        rows = find_unread_rows(frame, 537)
        self.assertIn(278, rows)
        self.assertNotIn(557, rows)

    def test_chat_geometry(self):
        frame = np.full((700, 1000, 3), 30, dtype=np.uint8)
        frame[70:560, 300:980] = 245
        frame[90:92, 300:980] = 30
        frame[150:152, 300:980] = 30
        frame[530:532, 300:980] = 30
        frame[560:570, 300:980] = 30
        area = locate_chat_area(frame)
        self.assertIsNotNone(area)
        self.assertEqual(area.x0, 300)
        self.assertGreater(area.y_top, 80)
        self.assertGreater(area.y_in, 500)

    def test_dark_theme_chat_geometry_ignores_matching_icon_rail(self):
        frame = np.full((700, 1000, 3), 30, dtype=np.uint8)
        frame[:, :300] = 47
        frame[:, 8:68] = 30
        frame[90:92, 300:980] = 47
        frame[150:152, 300:980] = 47
        frame[530:532, 300:980] = 47
        frame[560:570, 300:980] = 47
        area = locate_chat_area(frame)
        self.assertIsNotNone(area)
        self.assertEqual(area.x0, 300)
        self.assertEqual(area.background, (30, 30, 30))
        self.assertGreater(area.y_top, 100)
        self.assertEqual(area.y_in, 530)

    def test_chat_geometry_uses_header_when_content_fills_center(self):
        frame = np.full((700, 1000, 3), 30, dtype=np.uint8)
        frame[:, :300] = 47
        frame[:, 8:68] = 30
        frame[168:505, 300:980] = 245
        frame[90:92, 300:980] = 47
        frame[530:532, 300:980] = 47
        frame[560:570, 300:980] = 47
        area = locate_chat_area(frame)
        self.assertIsNotNone(area)
        self.assertEqual(area.x0, 300)
        self.assertEqual(area.y_in, 530)

    def test_chat_geometry_merges_nearby_right_edge_interruption(self):
        frame = np.full((700, 1000, 3), 47, dtype=np.uint8)
        frame[70:560, 300:980] = 30
        frame[:, 965:968] = 200
        frame[90:92, 300:980] = 47
        frame[150:152, 300:980] = 47
        frame[530:532, 300:980] = 47
        frame[560:570, 300:980] = 47
        area = locate_chat_area(frame)
        self.assertIsNotNone(area)
        self.assertEqual(area.x0, 300)
        self.assertEqual(area.x1, 980)
        self.assertEqual(area.y_in, 530)

    def test_black_or_login_frame_is_rejected(self):
        self.assertIsNone(locate_chat_area(np.zeros((700, 1000, 3), dtype=np.uint8)))

    def test_targeted_rows_keep_full_frame_coordinates(self):
        frame = np.full((700, 1000, 3), 250, dtype=np.uint8)
        ocr = WeChatOCR.__new__(WeChatOCR)
        box = [[10, 54], [110, 54], [110, 70], [10, 70]]
        ocr.reader = Mock(return_value=([(box, "客户甲", 0.9)], None))
        self.assertEqual(ocr.rows(frame, 300, centers=[200]), [(200.0, "客户甲")])

    def test_title_uses_header_and_preserves_spaces(self):
        frame = np.full((200, 1000, 3), 250, dtype=np.uint8)
        area = Mock(y_top=120, x0=300, x1=900)
        box = [[30, 60], [210, 60], [210, 96], [30, 96]]
        ocr = WeChatOCR.__new__(WeChatOCR)
        ocr.reader = Mock(return_value=([(box, "deep breath", 0.95)], None))
        self.assertEqual(ocr.title(frame, area), "deep breath")

    def test_image_messages_are_extracted_without_writing_files(self):
        frame = np.full((400, 600, 3), 235, dtype=np.uint8)
        cv2.rectangle(frame, (80, 80), (260, 220), (20, 80, 200), -1)
        cv2.circle(frame, (170, 150), 35, (240, 240, 240), -1)
        cv2.rectangle(frame, (360, 260), (540, 360), (30, 180, 90), -1)
        area = ChatArea(40, 50, 580, 370, (235, 235, 235), 40)
        ocr = WeChatOCR.__new__(WeChatOCR)
        images = ocr.images(frame, area)
        self.assertEqual([image.kind for image in images], ["in", "out"])
        self.assertTrue(all(image.data.startswith(b"\xff\xd8") for image in images))
        self.assertTrue(all(image.mime_type == "image/jpeg" for image in images))

    def test_click_row_restores_dpi_context(self):
        instance = Mock(left=0, top=0, width=1000, height=700, hwnd=1)
        with patch("nuochat.automation.resolve_instance", return_value=instance), \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation.time.sleep"):
            user32.SetThreadDpiAwarenessContext.side_effect = [123, 456]
            user32.ScreenToClient.return_value = 1
            automation.click_row("wechat:1:2", 200)
        self.assertEqual(user32.SetThreadDpiAwarenessContext.call_count, 2)
        self.assertEqual(user32.SetThreadDpiAwarenessContext.call_args_list[0].args[0].value,
                         automation._PER_MONITOR_AWARE_V2.value)
        self.assertEqual(user32.SetThreadDpiAwarenessContext.call_args_list[1].args[0], 123)

if __name__ == "__main__":
    unittest.main()
