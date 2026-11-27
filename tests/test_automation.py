import unittest
from unittest.mock import patch

import numpy as np

from nuochat import automation
from nuochat.models import ChatArea, WeChatInstance


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.instance = WeChatInstance(0x1234, 100, 200, 1000, 700)
        self.area = ChatArea(300, 150, 980, 600, (30, 30, 30), 60)

    def assert_no_foreground_input(self, user32):
        for name in (
            "GetForegroundWindow", "SetForegroundWindow", "BringWindowToTop",
            "AttachThreadInput", "SendInput", "keybd_event", "OpenClipboard",
        ):
            getattr(user32, name).assert_not_called()

    def test_fill_uses_window_messages_without_activating_wechat(self):
        with patch("nuochat.automation.resolve_instance", return_value=self.instance), \
                patch("nuochat.automation.restore_without_activation", return_value=False), \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation.time.sleep"):
            user32.PostMessageW.return_value = 1
            user32.MapVirtualKeyW.return_value = 28
            automation.fill_reply("wechat:100:200", self.area, "中😀")

        self.assert_no_foreground_input(user32)
        characters = [item.args[2] for item in user32.SendMessageW.call_args_list
                      if item.args[1] == 0x102]
        self.assertEqual(characters, [ord("中"), 0xD83D, 0xDE00])
        self.assertEqual(user32.PostMessageW.call_count, 3)
        self.assertEqual(user32.PostMessageW.call_args_list[0].args[3], (642 << 16) | 500)
        self.assertEqual(
            [item.args[1] for item in user32.SendMessageW.call_args_list[:4]],
            [0x100, 0x100, 0x101, 0x101],
        )

    def test_send_clicks_detected_background_send_button(self):
        frame = np.zeros((700, 1000, 3), dtype=np.uint8)
        frame[618:645, 942:970] = (25, 185, 116)
        area = ChatArea(300, 150, 980, 522, (30, 30, 30), 60)
        with patch("nuochat.automation.resolve_instance", return_value=self.instance), \
                patch("nuochat.automation.restore_without_activation", return_value=False), \
                patch("nuochat.automation.grab", return_value=(frame, None)), \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation.time.sleep"):
            automation.send_filled_reply("wechat:100:200", area)

        self.assert_no_foreground_input(user32)
        user32.PostMessageW.assert_not_called()
        self.assertEqual(user32.SendMessageW.call_count, 3)
        self.assertEqual(user32.SendMessageW.call_args_list[-1].args[:3],
                         (self.instance.hwnd, 0x0202, 0))
        self.assertEqual(user32.SendMessageW.call_args_list[-1].args[3],
                         (631 << 16) | 956)

    def test_send_stops_when_button_is_not_available(self):
        frame = np.zeros((700, 1000, 3), dtype=np.uint8)
        with patch("nuochat.automation.resolve_instance", return_value=self.instance), \
                patch("nuochat.automation.restore_without_activation", return_value=False), \
                patch("nuochat.automation.grab", return_value=(frame, None)), \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "发送按钮"):
                automation.send_filled_reply("wechat:100:200", self.area)
        user32.SendMessageW.assert_not_called()

    def test_background_click_failure_stops_fill(self):
        with patch("nuochat.automation.resolve_instance", return_value=self.instance), \
                patch("nuochat.automation.restore_without_activation", return_value=False), \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation.time.sleep"):
            user32.PostMessageW.return_value = 0
            with self.assertRaisesRegex(RuntimeError, "拒绝"):
                automation.fill_reply("wechat:100:200", self.area, "测试")
        user32.SendMessageW.assert_not_called()

    def test_minimized_window_is_restored_without_activation(self):
        restored = WeChatInstance(0x1234, 100, 200, 1100, 750)
        with patch("nuochat.automation.resolve_instance",
                   side_effect=[self.instance, restored]) as resolve, \
                patch("nuochat.automation.restore_without_activation", return_value=True) as restore, \
                patch("nuochat.automation.u32") as user32, \
                patch("nuochat.automation._click_client") as click, \
                patch("nuochat.automation._press_ctrl_end"), \
                patch("nuochat.automation._type_text"), \
                patch("nuochat.automation.time.sleep"):
            automation.fill_reply("wechat:100:200", self.area, "测试")

        restore.assert_called_once_with(self.instance.hwnd)
        self.assertEqual(resolve.call_count, 2)
        click.assert_called_once_with(restored, 550, 642)
        self.assert_no_foreground_input(user32)


if __name__ == "__main__":
    unittest.main()
