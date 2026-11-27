import unittest
from dataclasses import replace
from unittest.mock import Mock, call, patch

import numpy as np

from nuochat.chat import TEST_CHAT_TITLE
from nuochat.config import AppConfig
from nuochat.engine import LLMResult, LLMUsage
from nuochat.models import ChatImage, MessageLine
from nuochat.worker import CopilotWorker, ReplyDraft


class WorkerTests(unittest.TestCase):
    def make_worker(self, auto_send=False):
        worker = CopilotWorker()
        worker.config = AppConfig(auto_send=auto_send, random_reply_delay=False)
        worker.api_key = "test-only"
        worker.llm = Mock()
        worker.llm.generate_with_usage.return_value = LLMResult(
            "建议回复", LLMUsage(100, 20, 120), "test-model",
        )
        return worker

    def test_manual_mode_emits_draft_without_sending(self):
        worker = self.make_worker()
        drafts = []
        worker.signals.draft.connect(drafts.append)
        worker._send = Mock()
        lines = [MessageLine("in", "客户问题", 10)]
        worker._generate_reply("wechat:1:2", "客户甲", "客户问题", lines)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].text, "建议回复")
        worker._send.assert_not_called()

    def test_generation_records_activity_and_token_usage(self):
        worker = self.make_worker()
        activities = []
        worker.signals.activity.connect(activities.append)
        worker._generate_reply("wechat:1:2", "客户甲", "客户问题", [])
        self.assertEqual(len(activities), 1)
        record = activities[0]
        self.assertEqual(record.title, "客户甲")
        self.assertEqual(record.reply, "建议回复")
        self.assertEqual(record.status, "待审核")
        self.assertEqual(record.prompt_tokens, 100)
        self.assertEqual(record.completion_tokens, 20)
        self.assertEqual(record.total_tokens, 120)
        self.assertEqual(record.model, "test-model")

    def test_auto_mode_dispatches_send_immediately(self):
        worker = self.make_worker(auto_send=True)
        worker._send = Mock()
        worker._generate_reply("wechat:1:2", "客户甲", "客户问题", [])
        worker._send.assert_called_once()
        self.assertEqual(worker._send.call_args.args[0].title, "客户甲")

    def test_auto_mode_waits_for_random_interval(self):
        worker = self.make_worker(auto_send=True)
        worker.config = replace(
            worker.config, random_reply_delay=True,
            random_delay_min=10.0, random_delay_max=15.0,
        )
        worker._send = Mock()
        with patch("nuochat.worker.random.uniform", return_value=12.0), \
                patch("nuochat.worker.time.monotonic", return_value=100.0):
            worker._generate_reply("wechat:1:2", "客户甲", "客户问题", [])
        worker._send.assert_not_called()
        request_id = next(iter(worker.drafts))
        self.assertEqual(worker.pending_sends, [(112.0, request_id)])
        with patch("nuochat.worker.time.monotonic", return_value=111.9):
            worker._dispatch_pending_sends()
        worker._send.assert_not_called()
        with patch("nuochat.worker.time.monotonic", return_value=112.0):
            worker._dispatch_pending_sends()
        worker._send.assert_called_once()
        self.assertEqual(worker.pending_sends, [])

    def test_scan_pauses_while_auto_send_is_pending(self):
        worker = self.make_worker(auto_send=True)
        worker.instance_id = "wechat:1:2"
        worker.pending_sends = [(200.0, "request-1")]
        worker._snapshot = Mock()
        with patch("nuochat.worker.automation.click_row") as click:
            worker._scan_once()
        worker._snapshot.assert_not_called()
        click.assert_not_called()

    def test_paused_monitoring_skips_scan_and_pending_send(self):
        worker = self.make_worker(auto_send=True)
        worker.config = replace(worker.config, monitoring=False)
        worker.instance_id = "wechat:1:2"
        worker.pending_sends = [(0.0, "request-1")]
        worker._snapshot = Mock()
        worker._send = Mock()
        worker._scan_once()
        worker._dispatch_pending_sends()
        worker._snapshot.assert_not_called()
        worker._send.assert_not_called()

    def test_repeated_scan_error_is_logged_once_and_backed_off(self):
        worker = self.make_worker()
        logs = []
        worker.signals.log.connect(lambda category, message: logs.append((category, message)))
        delays = [worker._report_scan_error(RuntimeError("未找到聊天区域")) for _ in range(7)]
        self.assertEqual(len(logs), 2)
        self.assertGreater(delays[-1], delays[0])

    def test_disabling_auto_send_returns_pending_draft_for_review(self):
        worker = self.make_worker(auto_send=True)
        worker.config = replace(worker.config, random_reply_delay=True)
        worker._send = Mock()
        with patch("nuochat.worker.random.uniform", return_value=20.0), \
                patch("nuochat.worker.time.monotonic", return_value=100.0):
            worker._generate_reply("wechat:1:2", "客户甲", "客户问题", [])
        drafts = []
        worker.signals.draft.connect(drafts.append)
        with patch("nuochat.worker.set_sleep_inhibition", return_value=True):
            worker._apply_config(replace(worker.config, auto_send=False), "test-only")
        self.assertEqual(worker.pending_sends, [])
        self.assertEqual(len(drafts), 1)
        worker._send.assert_not_called()

    def test_monitoring_power_setting_follows_config(self):
        worker = self.make_worker()
        with patch("nuochat.worker.set_sleep_inhibition", return_value=True) as power:
            worker._apply_config(
                replace(worker.config, monitoring=True, keep_awake_when_monitoring=True),
                "test-only",
            )
            worker._apply_config(
                replace(worker.config, monitoring=False, keep_awake_when_monitoring=True),
                "test-only",
            )
        self.assertEqual([call.args[0] for call in power.call_args_list], [True, False])
        self.assertFalse(worker._sleep_inhibited)

    def test_system_chat_never_calls_model(self):
        worker = self.make_worker()
        worker._generate_reply("wechat:1:2", "微信团队", "通知", [])
        worker.llm.generate_with_usage.assert_not_called()
        self.assertEqual(worker.drafts, {})

    def test_test_mode_auto_sends_to_file_transfer_assistant(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, test_mode=True, auto_send=False)
        worker._send = Mock()
        worker._generate_reply("wechat:1:2", TEST_CHAT_TITLE, "测试消息", [])
        worker.llm.generate_with_usage.assert_called_once()
        worker._send.assert_called_once()
        self.assertEqual(worker._send.call_args.args[0].title, TEST_CHAT_TITLE)

    def test_test_mode_blocks_other_conversations(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, test_mode=True)
        worker._generate_reply("wechat:1:2", "客户甲", "测试消息", [])
        worker.llm.generate_with_usage.assert_not_called()
        self.assertEqual(worker.drafts, {})

    def test_test_mode_scan_switches_only_to_file_transfer_assistant(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, test_mode=True, auto_switch=False)
        worker.instance_id = "wechat:1:2"
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        area = Mock(x0=0, x1=300, y_top=10, y_in=100)
        instance = Mock(instance_id="wechat:1:2")
        worker._snapshot = Mock(side_effect=[(instance, frame, area), (instance, frame, area)])
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.side_effect = ["客户甲", TEST_CHAT_TITLE]
        worker._get_ocr.return_value.rows.return_value = [(100, TEST_CHAT_TITLE), (200, "客户乙")]
        worker._get_ocr.return_value.messages.return_value = [MessageLine("in", "测试消息", 20)]
        worker._generate_reply = Mock()
        with patch("nuochat.worker.capture.find_unread_rows", return_value=[100, 200]), \
                patch("nuochat.worker.automation.click_row") as click:
            worker._scan_once()
        click.assert_called_once_with("wechat:1:2", 100)
        worker._generate_reply.assert_called_once_with(
            "wechat:1:2", TEST_CHAT_TITLE, "测试消息",
            worker._get_ocr.return_value.messages.return_value, [],
        )

    def test_test_mode_send_rejects_similarly_named_conversation(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, test_mode=True)
        draft = ReplyDraft("request-1", "wechat:1:2", TEST_CHAT_TITLE, "建议回复", "测试消息")
        worker.drafts[draft.request_id] = draft
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), None, Mock()))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "文件传输助手测试"
        with patch("nuochat.worker.automation.fill_reply") as fill, \
                patch("nuochat.worker.automation.send_filled_reply") as send:
            worker._send(draft)
        fill.assert_not_called()
        send.assert_not_called()
        self.assertIn(draft.request_id, worker.drafts)

    def test_send_fills_reply_and_marks_message_sent(self):
        worker = self.make_worker()
        resolved = []
        worker.signals.activity_resolved.connect(lambda request_id, status: resolved.append((request_id, status)))
        draft = ReplyDraft("request-1", "wechat:1:2", "客户甲", "建议回复", "客户问题")
        worker.drafts[draft.request_id] = draft
        instance = Mock(instance_id="wechat:1:2")
        area = Mock()
        worker._snapshot = Mock(return_value=(instance, None, area))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户甲"
        with patch("nuochat.worker.automation.fill_reply") as fill, \
                patch("nuochat.worker.automation.send_filled_reply") as send:
            worker._send(draft)
        fill.assert_called_once_with("wechat:1:2", area, "建议回复")
        send.assert_called_once_with("wechat:1:2", area)
        self.assertFalse(worker.guard.check("客户甲", "客户问题").allowed)
        self.assertNotIn(draft.request_id, worker.drafts)
        self.assertEqual(resolved, [("request-1", "已发送")])

    def test_send_stops_when_conversation_title_changed(self):
        worker = self.make_worker()
        draft = ReplyDraft("request-1", "wechat:1:2", "客户甲", "建议回复", "客户问题")
        worker.drafts[draft.request_id] = draft
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), None, Mock()))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户乙"
        with patch("nuochat.worker.automation.fill_reply") as fill, \
                patch("nuochat.worker.automation.send_filled_reply") as send:
            worker._send(draft)
        fill.assert_not_called()
        send.assert_not_called()
        self.assertIn(draft.request_id, worker.drafts)

    def test_failed_send_returns_draft_for_review(self):
        worker = self.make_worker(auto_send=True)
        draft = ReplyDraft("request-1", "wechat:1:2", "客户甲", "建议回复", "客户问题")
        worker.drafts[draft.request_id] = draft
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), None, Mock()))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户乙"
        drafts = []
        worker.signals.draft.connect(drafts.append)
        worker._send(draft)
        self.assertEqual(drafts, [draft])
        self.assertIn(draft.request_id, worker.drafts)

    def test_scan_generates_only_latest_unread_reply(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, auto_switch=False)
        worker.instance_id = "wechat:1:2"
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        area = Mock(x0=0, x1=300, y_top=10, y_in=100)
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), frame, area))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户甲"
        worker._get_ocr.return_value.messages.return_value = [
            MessageLine("in", "第一条", 20), MessageLine("in", "最新一条", 30),
        ]
        memory = worker.memory.get("客户甲")
        memory.initialized = True
        memory.floor_y = 10
        worker._generate_reply = Mock()
        worker._scan_once()
        worker._generate_reply.assert_called_once_with(
            "wechat:1:2", "客户甲", "最新一条", worker._get_ocr.return_value.messages.return_value, [])

    def test_scan_generates_reply_for_image_only_message(self):
        worker = self.make_worker()
        worker.config = replace(worker.config, auto_switch=False, understand_images=True)
        worker.instance_id = "wechat:1:2"
        frame = np.zeros((200, 300, 3), dtype=np.uint8)
        area = Mock(x0=0, x1=300, y_top=10, y_in=100)
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), frame, area))
        image = ChatImage("in", 30, b"image-bytes")
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户甲"
        worker._get_ocr.return_value.messages.return_value = []
        worker._get_ocr.return_value.images.return_value = [image]
        memory = worker.memory.get("客户甲")
        memory.initialized = True
        memory.images_initialized = True
        memory.image_floor_y = 10
        worker._generate_reply = Mock()
        worker._scan_once()
        args = worker._generate_reply.call_args.args
        self.assertTrue(args[2].startswith("[图片:"))
        self.assertEqual(args[3], [])
        self.assertEqual(args[4], [image])

    def test_generate_reply_passes_images_to_model(self):
        worker = self.make_worker()
        image = ChatImage("in", 20, b"image-bytes")
        worker._generate_reply("wechat:1:2", "客户甲", "[图片]", [], images=[image])
        self.assertEqual(worker.llm.generate_with_usage.call_args.kwargs["images"], [image])

    def test_send_splits_reply_with_random_gaps(self):
        worker = self.make_worker()
        draft = ReplyDraft(
            "request-1", "wechat:1:2", "客户甲", "第一句。第二句！第三句？", "客户问题")
        worker.drafts[draft.request_id] = draft
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), None, Mock()))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户甲"
        with patch("nuochat.worker.automation.fill_reply") as fill, \
                patch("nuochat.worker.automation.send_filled_reply") as send, \
                patch("nuochat.worker.random.uniform", return_value=2.5), \
                patch("nuochat.worker.time.sleep") as sleep:
            worker._send(draft)
        self.assertEqual([item.args[2] for item in fill.call_args_list],
                         ["第一句。", "第二句！", "第三句？"])
        self.assertEqual(send.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(2.5), call(2.5)])
        self.assertNotIn(draft.request_id, worker.drafts)

    def test_partial_send_keeps_only_unsent_chunks_for_review(self):
        worker = self.make_worker(auto_send=True)
        draft = ReplyDraft(
            "request-1", "wechat:1:2", "客户甲", "第一句。第二句！第三句？", "客户问题")
        worker.drafts[draft.request_id] = draft
        worker._snapshot = Mock(return_value=(Mock(instance_id="wechat:1:2"), None, Mock()))
        worker._get_ocr = Mock()
        worker._get_ocr.return_value.title.return_value = "客户甲"
        drafts = []
        worker.signals.draft.connect(drafts.append)
        with patch("nuochat.worker.automation.fill_reply"), \
                patch("nuochat.worker.automation.send_filled_reply",
                      side_effect=[None, RuntimeError("输入失败")]), \
                patch("nuochat.worker.random.uniform", return_value=2.0), \
                patch("nuochat.worker.time.sleep"):
            worker._send(draft)
        self.assertEqual(worker.drafts[draft.request_id].text, "第二句！\n第三句？")
        self.assertEqual(drafts[-1].text, "第二句！\n第三句？")

    def test_discard_updates_activity_status(self):
        worker = self.make_worker()
        draft = ReplyDraft("request-1", "wechat:1:2", "客户甲", "建议回复", "客户问题")
        worker.drafts[draft.request_id] = draft
        resolved = []
        worker.signals.activity_resolved.connect(lambda request_id, status: resolved.append((request_id, status)))
        worker._command("discard", draft.request_id)
        self.assertEqual(resolved, [("request-1", "已丢弃")])
        self.assertNotIn(draft.request_id, worker.drafts)

    def test_instance_change_resets_account_scoped_guard(self):
        worker = self.make_worker()
        worker.guard.mark_sent("同名客户", "在吗", now=100)
        worker._command("instance", "wechat:3:4")
        self.assertTrue(worker.guard.check("同名客户", "在吗", now=101).allowed)


if __name__ == "__main__":
    unittest.main()
