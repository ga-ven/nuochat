import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path

from .config import default_config_path


PENDING_STATUSES = {"待审核", "自动发送中", "发送失败"}


@dataclass(frozen=True)
class ActivityRecord:
    request_id: str
    timestamp: str
    title: str
    reply: str
    source_text: str
    status: str
    auto_send: bool
    had_image: bool
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


@dataclass(frozen=True)
class ActivitySummary:
    today_generated: int = 0
    today_sent: int = 0
    today_auto_sent: int = 0
    today_manual_sent: int = 0
    today_prompt_tokens: int = 0
    today_completion_tokens: int = 0
    today_total_tokens: int = 0
    total_sent: int = 0
    total_tokens: int = 0


def default_stats_path() -> Path:
    return default_config_path().with_name("stats.json")


def _record_date(record: ActivityRecord):
    try:
        return datetime.fromisoformat(record.timestamp).date()
    except (TypeError, ValueError):
        return date.min


class ActivityStore:
    def __init__(self, path=None, max_records=1000):
        self.path = Path(path) if path else default_stats_path()
        self.max_records = max_records
        self._records = self._load()

    def _load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        records = []
        for item in payload[-self.max_records:]:
            if not isinstance(item, dict):
                continue
            try:
                records.append(ActivityRecord(
                    request_id=str(item["request_id"]),
                    timestamp=str(item["timestamp"]),
                    title=str(item.get("title", "")),
                    reply=str(item.get("reply", "")),
                    source_text=str(item.get("source_text", "")),
                    status=str(item.get("status", "待审核")),
                    auto_send=bool(item.get("auto_send", False)),
                    had_image=bool(item.get("had_image", False)),
                    prompt_tokens=max(0, int(item.get("prompt_tokens", 0))),
                    completion_tokens=max(0, int(item.get("completion_tokens", 0))),
                    total_tokens=max(0, int(item.get("total_tokens", 0))),
                    model=str(item.get("model", "")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return records

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [asdict(record) for record in self._records],
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        handle, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", dir=self.path.parent,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def add(self, record: ActivityRecord):
        self._records = [item for item in self._records if item.request_id != record.request_id]
        self._records.append(record)
        self._records = self._records[-self.max_records:]
        self.save()

    def resolve(self, request_id: str, status: str):
        changed = False
        updated = []
        for record in self._records:
            if record.request_id == request_id:
                record = replace(record, status=status)
                changed = True
            updated.append(record)
        if changed:
            self._records = updated
            self.save()

    def recent(self, limit=8):
        return list(reversed(self._records[-limit:]))

    def summary(self, today=None):
        today = today or datetime.now().astimezone().date()
        today_records = [record for record in self._records if _record_date(record) == today]
        sent = [record for record in today_records if record.status == "已发送"]
        return ActivitySummary(
            today_generated=len(today_records),
            today_sent=len(sent),
            today_auto_sent=sum(record.auto_send for record in sent),
            today_manual_sent=sum(not record.auto_send for record in sent),
            today_prompt_tokens=sum(record.prompt_tokens for record in today_records),
            today_completion_tokens=sum(record.completion_tokens for record in today_records),
            today_total_tokens=sum(record.total_tokens for record in today_records),
            total_sent=sum(record.status == "已发送" for record in self._records),
            total_tokens=sum(record.total_tokens for record in self._records),
        )
