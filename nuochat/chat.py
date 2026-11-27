import hashlib
import re
from collections import OrderedDict

from .models import MessageLine, UnreadCandidate

TEST_CHAT_TITLE = "文件传输助手"
SYSTEM_CHATS = (
    "微信团队", TEST_CHAT_TITLE, "腾讯新闻", "订阅号", "服务号", "公众号",
    "微信支付", "微信运动", "微信收款助手", "折叠的群聊",
)
_GROUP_SUFFIX = re.compile(r"[（(]\s*\d+\s*[)）]\s*$")
_TIME_ONLY = re.compile(r"\d{1,2}:\d{2}")
_COUNT_ONLY = re.compile(r"\d+")


def normalize_title(title: str) -> str:
    title = _GROUP_SUFFIX.sub("", title or "")
    return re.sub(r"[\s…\.]+", "", title)


def is_system_chat(title: str) -> bool:
    return any(name in (title or "") for name in SYSTEM_CHATS)


def is_group_title(title: str) -> bool:
    return bool(_GROUP_SUFFIX.search(title or ""))


def is_test_chat(title: str) -> bool:
    return normalize_title(title) == normalize_title(TEST_CHAT_TITLE)


def message_key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", "", text or "").encode("utf-8")).hexdigest()


def match_unread_rows(badge_rows, ocr_rows, current_title: str, x0: int,
                      skip_system=True, only_title=None):
    tolerance = max(24, int(x0 * 0.12))
    current = normalize_title(current_title)
    candidates = []
    for badge_y in badge_rows:
        nearby = sorted(
            (float(row_y), text.strip())
            for row_y, text in ocr_rows
            if abs(float(row_y) - badge_y) <= tolerance
            and text.strip() and not _TIME_ONLY.fullmatch(text.strip())
            and not _COUNT_ONLY.fullmatch(text.strip())
        )
        if not nearby:
            continue
        title = re.sub(
            r"(?:今天|昨天|星期[一二三四五六日天])?\s*\d{1,2}:\d{2}\s*$",
            "", nearby[0][1],
        ).strip()
        if not title or _TIME_ONLY.fullmatch(title):
            continue
        if title.startswith("[") or re.search(r"[:：]", title):
            continue
        normalized = normalize_title(title)
        if only_title and normalized != normalize_title(only_title):
            continue
        if current and (current.startswith(normalized) or normalized.startswith(current)):
            continue
        combined = " ".join(text for _, text in nearby)
        if not skip_system or not is_system_chat(combined):
            candidate = UnreadCandidate(int(nearby[0][0]), title)
            duplicate = any(
                abs(candidate.y - existing.y) <= max(12, int(x0 * 0.03))
                and normalize_title(candidate.title) == normalize_title(existing.title)
                for existing in candidates
            )
            if not duplicate:
                candidates.append(candidate)
    return candidates


def split_reply_chunks(text: str, max_chars=180):
    paragraphs = [part.strip() for part in re.split(r"\n+", text or "") if part.strip()]
    chunks = []
    for paragraph in paragraphs:
        pieces = re.split(
            r"(?<=[。！？!?])\s*|(?<=[；;])\s*|(?<=[.!?])\s+(?=[A-Z0-9\u4e00-\u9fff“\"'“‘])",
            paragraph,
        )
        for piece in pieces:
            piece = piece.strip()
            while len(piece) > max_chars:
                cut = max(piece.rfind("，", 0, max_chars),
                          piece.rfind(",", 0, max_chars),
                          piece.rfind(" ", 0, max_chars))
                if cut < max_chars // 2:
                    cut = max_chars
                chunks.append(piece[:cut].strip())
                piece = piece[cut:].strip()
            if piece:
                chunks.append(piece)
    return chunks


class ConversationMemory:
    def __init__(self):
        self.initialized = False
        self.floor_y = -1.0
        self.seen = set()
        self.emitted = set()
        self.images_initialized = False
        self.image_floor_y = -1.0
        self.image_seen = set()
        self.image_emitted = set()

    def observe(self, lines, allow_latest=False):
        incoming = sorted((line for line in lines if line.kind == "in"), key=lambda line: line.y)
        fresh = []
        if not self.initialized:
            if allow_latest and incoming:
                last = incoming[-1]
                key = message_key(last.text)
                if key not in self.emitted:
                    fresh.append(last)
                    self.emitted.add(key)
            self.initialized = True
        else:
            for line in incoming:
                key = message_key(line.text)
                if line.y > self.floor_y and key not in self.seen and key not in self.emitted:
                    fresh.append(line)
                    self.emitted.add(key)
        self.seen.update(message_key(line.text) for line in incoming)
        if lines:
            self.floor_y = max(self.floor_y, max(float(line.y) for line in lines))
        if len(self.seen) > 500:
            self.seen = set(list(self.seen)[-400:])
        return fresh

    def observe_images(self, images, allow_latest=False):
        incoming = sorted((image for image in images if image.kind == "in"),
                          key=lambda image: image.y)
        fresh = []
        if not self.images_initialized:
            if allow_latest and incoming:
                last = incoming[-1]
                key = hashlib.sha1(last.data).hexdigest()
                if key not in self.image_emitted:
                    fresh.append(last)
                    self.image_emitted.add(key)
            self.images_initialized = True
        else:
            for image in incoming:
                key = hashlib.sha1(image.data).hexdigest()
                if image.y > self.image_floor_y and key not in self.image_seen \
                        and key not in self.image_emitted:
                    fresh.append(image)
                    self.image_emitted.add(key)
        self.image_seen.update(hashlib.sha1(image.data).hexdigest() for image in incoming)
        if images:
            self.image_floor_y = max(self.image_floor_y,
                                     max(float(image.y) for image in images))
        if len(self.image_seen) > 200:
            self.image_seen = set(list(self.image_seen)[-150:])
        return fresh


class ConversationStore:
    def __init__(self, limit=50):
        self.limit = limit
        self.items = OrderedDict()

    def get(self, title: str):
        memory = self.items.get(title)
        if memory is None:
            memory = ConversationMemory()
            self.items[title] = memory
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return memory

    def clear(self):
        self.items.clear()
