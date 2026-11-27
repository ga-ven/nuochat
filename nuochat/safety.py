import time
from collections import deque
from dataclasses import dataclass

from .chat import is_group_title, is_system_chat, is_test_chat, message_key


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class ReplyGuard:
    def __init__(self, cooldown=8.0, hourly_limit=30):
        self.cooldown = max(0.0, float(cooldown))
        self.hourly_limit = max(1, int(hourly_limit))
        self.last_sent = {}
        self.sent_keys = set()
        self.sent_times = deque()

    def check(self, title: str, text: str, reply_groups=True, skip_system=True, now=None,
              test_mode=False):
        now = time.monotonic() if now is None else now
        if test_mode:
            if not is_test_chat(title):
                return GuardDecision(False, "测试模式只允许文件传输助手")
        elif skip_system and is_system_chat(title):
            return GuardDecision(False, "系统会话已跳过")
        if is_group_title(title) and not reply_groups:
            return GuardDecision(False, "群聊回复已关闭")
        key = (title, message_key(text))
        if key in self.sent_keys:
            return GuardDecision(False, "这条消息已经回复过")
        last = self.last_sent.get(title)
        if last is not None and now - last < self.cooldown:
            return GuardDecision(False, "会话冷却中")
        while self.sent_times and now - self.sent_times[0] >= 3600:
            self.sent_times.popleft()
        if len(self.sent_times) >= self.hourly_limit:
            return GuardDecision(False, "已达到每小时回复上限")
        return GuardDecision(True)

    def mark_sent(self, title: str, text: str, now=None):
        now = time.monotonic() if now is None else now
        self.last_sent[title] = now
        self.sent_keys.add((title, message_key(text)))
        self.sent_times.append(now)
        if len(self.sent_keys) > 1000:
            self.sent_keys = set(list(self.sent_keys)[-800:])
