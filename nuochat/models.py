from dataclasses import dataclass


@dataclass(frozen=True)
class WeChatInstance:
    hwnd: int
    pid: int
    started: int
    width: int
    height: int
    left: int = 0
    top: int = 0
    title: str = "微信"

    @property
    def instance_id(self) -> str:
        return f"wechat:{self.pid}:{self.started}"


@dataclass(frozen=True)
class ChatArea:
    x0: int
    y_top: int
    x1: int
    y_in: int
    background: tuple[int, int, int]
    y_pane: int


@dataclass(frozen=True)
class MessageLine:
    kind: str
    text: str
    y: float


@dataclass(frozen=True)
class ChatImage:
    kind: str
    y: float
    data: bytes
    mime_type: str = "image/jpeg"

    @property
    def text(self):
        return "[图片]"


@dataclass(frozen=True)
class UnreadCandidate:
    y: int
    title: str
