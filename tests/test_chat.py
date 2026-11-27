import unittest

from nuochat.chat import (
    TEST_CHAT_TITLE, ConversationMemory, is_group_title, is_system_chat, is_test_chat,
    match_unread_rows, message_key, normalize_title, split_reply_chunks,
)
from nuochat.models import ChatImage, MessageLine


class ChatTests(unittest.TestCase):
    def test_titles(self):
        self.assertEqual(normalize_title(" 项目群（3） "), "项目群")
        self.assertTrue(is_group_title("项目群(12)"))
        self.assertTrue(is_system_chat("微信团队：新通知"))
        self.assertFalse(is_system_chat("客户张三"))

    def test_test_chat_title_matching_is_exact(self):
        self.assertTrue(is_test_chat(TEST_CHAT_TITLE))
        self.assertTrue(is_test_chat(" 文件传输助手 "))
        self.assertFalse(is_test_chat("文件传输助手测试"))
        self.assertFalse(is_test_chat("客户甲"))

    def test_unread_matching_can_filter_to_test_chat(self):
        rows = [(100, TEST_CHAT_TITLE), (200, "客户乙")]
        candidates = match_unread_rows(
            [100, 200], rows, "客户甲", 300, skip_system=False,
            only_title=TEST_CHAT_TITLE,
        )
        self.assertEqual([item.title for item in candidates], [TEST_CHAT_TITLE])

    def test_unread_matching_finds_test_chat_from_similarly_named_current_chat(self):
        rows = [(100, TEST_CHAT_TITLE), (200, "文件传输助手测试")]
        candidates = match_unread_rows(
            [100], rows, "文件传输助手测试", 300,
            skip_system=False, only_title=TEST_CHAT_TITLE,
        )
        self.assertEqual([item.title for item in candidates], [TEST_CHAT_TITLE])

    def test_unread_matching_fails_closed(self):
        rows = [(200, "客户甲"), (205, "10:24"), (400, "微信团队"),
                (500, "项目群（4）"), (600, "在吗")]
        current = "客户甲"
        candidates = match_unread_rows([204, 404, 504, 600], rows, current, 300)
        self.assertEqual([item.title for item in candidates], ["项目群（4）", "在吗"])
        self.assertEqual(match_unread_rows([700], [], "", 300), [])

    def test_unread_matching_ignores_preview_but_accepts_timestamped_title(self):
        rows = [(200, "客户甲昨天21:12"), (205, "10:24"), (400, "微信团队"),
                (500, "项目群（4）"), (600, "昨天10:26")]
        candidates = match_unread_rows([204, 600], rows, "", 300)
        self.assertEqual([item.title for item in candidates], ["客户甲"])

    def test_unread_matching_ignores_badge_count(self):
        rows = [(200, "1"), (205, "客户甲今天10:24")]
        candidates = match_unread_rows([204], rows, "", 300)
        self.assertEqual([item.title for item in candidates], ["客户甲"])

    def test_unread_matching_deduplicates_same_badge_row(self):
        rows = [(200, "deep breath08:14"), (205, "最新消息预览")]
        candidates = match_unread_rows([204, 214], rows, "", 300)
        self.assertEqual([item.title for item in candidates], ["deep breath"])
        self.assertEqual(match_unread_rows([204], rows, "deepbreath", 300), [])

    def test_split_reply_chunks_preserves_natural_sentences(self):
        self.assertEqual(
            split_reply_chunks("你好！很高兴认识你。\n请问有什么可以帮你？"),
            ["你好！", "很高兴认识你。", "请问有什么可以帮你？"],
        )
        self.assertEqual(
            split_reply_chunks("Hello! How can I help? I am here."),
            ["Hello!", "How can I help?", "I am here."],
        )
        self.assertEqual(
            split_reply_chunks("版本号是3.14，明天见。好的"),
            ["版本号是3.14，明天见。", "好的"],
        )

    def test_memory_baseline_and_new_messages(self):
        memory = ConversationMemory()
        lines = [MessageLine("in", "历史消息", 10), MessageLine("out", "历史回复", 20)]
        self.assertEqual(memory.observe(lines), [])
        self.assertEqual(memory.observe(lines), [])
        fresh = memory.observe(lines + [MessageLine("in", "新消息", 30)])
        self.assertEqual([line.text for line in fresh], ["新消息"])
        self.assertEqual(memory.observe(lines + [MessageLine("in", "新消息", 30)]), [])

    def test_memory_unread_emits_latest_once(self):
        memory = ConversationMemory()
        lines = [MessageLine("in", "第一条", 10), MessageLine("in", "最新一条", 20)]
        self.assertEqual([line.text for line in memory.observe(lines, True)], ["最新一条"])
        self.assertEqual(memory.observe(lines, True), [])
        self.assertEqual(message_key("你好 世界"), message_key("你好世界"))

    def test_image_memory_baseline_and_new_image(self):
        memory = ConversationMemory()
        first = ChatImage("in", 10, b"first-image")
        second = ChatImage("in", 20, b"second-image")
        self.assertEqual(memory.observe_images([first]), [])
        self.assertEqual(memory.observe_images([first, second]), [second])
        self.assertEqual(memory.observe_images([first, second]), [])

    def test_image_memory_unread_emits_latest_once(self):
        memory = ConversationMemory()
        first = ChatImage("in", 10, b"first-image")
        second = ChatImage("in", 20, b"second-image")
        self.assertEqual(memory.observe_images([first, second], True), [second])
        self.assertEqual(memory.observe_images([first, second], True), [])


if __name__ == "__main__":
    unittest.main()
