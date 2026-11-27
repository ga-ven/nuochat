import unittest

from nuochat.safety import ReplyGuard


class SafetyTests(unittest.TestCase):
    def test_system_and_group_policy(self):
        guard = ReplyGuard()
        self.assertFalse(guard.check("微信团队", "你好").allowed)
        self.assertFalse(guard.check("项目群（3）", "你好", reply_groups=False).allowed)
        self.assertTrue(guard.check("项目群（3）", "你好", reply_groups=True).allowed)

    def test_test_mode_only_allows_file_transfer_assistant(self):
        guard = ReplyGuard()
        self.assertTrue(guard.check("文件传输助手", "测试消息", test_mode=True).allowed)
        self.assertFalse(guard.check("客户甲", "测试消息", test_mode=True).allowed)
        self.assertFalse(guard.check("文件传输助手测试", "测试消息", test_mode=True).allowed)

    def test_duplicate_is_per_conversation(self):
        guard = ReplyGuard(cooldown=0)
        guard.mark_sent("客户甲", "在吗", now=100)
        self.assertFalse(guard.check("客户甲", "在吗", now=101).allowed)
        self.assertTrue(guard.check("客户乙", "在吗", now=101).allowed)

    def test_cooldown_and_hourly_limit(self):
        guard = ReplyGuard(cooldown=8, hourly_limit=2)
        self.assertTrue(guard.check("客户甲", "第一条", now=100).allowed)
        guard.mark_sent("客户甲", "第一条", now=100)
        self.assertFalse(guard.check("客户甲", "第二条", now=105).allowed)
        self.assertTrue(guard.check("客户乙", "第二条", now=105).allowed)
        guard.mark_sent("客户乙", "第二条", now=105)
        self.assertFalse(guard.check("客户丙", "第三条", now=106).allowed)
        self.assertTrue(guard.check("客户丙", "第三条", now=3706).allowed)


if __name__ == "__main__":
    unittest.main()
