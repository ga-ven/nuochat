import hashlib
import queue
import random
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from . import automation, capture
from .chat import (
    TEST_CHAT_TITLE, ConversationStore, is_group_title, is_system_chat, is_test_chat,
    normalize_title, split_reply_chunks,
)
from .config import AppConfig
from .engine import LLMClient, LLMError
from .ocr import WeChatOCR
from .power import set_sleep_inhibition
from .safety import ReplyGuard
from .stats import ActivityRecord


@dataclass(frozen=True)
class ReplyDraft:
    request_id: str
    instance_id: str
    title: str
    text: str
    source_text: str


class CopilotSignals(QObject):
    instances = Signal(list)
    status = Signal(str)
    log = Signal(str, str)
    draft = Signal(object)
    draft_resolved = Signal(str, bool)
    activity = Signal(object)
    activity_resolved = Signal(str, str)
    sent = Signal(str, str)
    connected = Signal(bool)


class CopilotWorker(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = CopilotSignals()
        self.commands = queue.Queue()
        self.config = AppConfig()
        self.api_keys = {}
        self.instance_id = ""
        self.memory = ConversationStore()
        self.guard = ReplyGuard()
        self.llm = LLMClient()
        self.ocr = None
        self.drafts = {}
        self.pending_sends = []
        self.message_hashes = {}
        self.current_title = ""
        self._stopped = False
        self._sleep_inhibited = False
        self._last_scan_error = ""
        self._scan_error_count = 0

    def stop(self):
        self._stopped = True
        self.commands.put(("stop", None))

    def configure(self, config, api_keys):
        self.commands.put(("configure", (config, api_keys)))

    def select_instance(self, instance_id):
        self.commands.put(("instance", instance_id))

    def request_scan(self):
        self.commands.put(("scan", None))

    def refresh_instances(self):
        self.commands.put(("instances", None))

    def test_connection(self):
        self.commands.put(("test", None))

    def fill_draft(self, request_id):
        self.commands.put(("fill", request_id))

    def send_draft(self, request_id):
        self.commands.put(("send", request_id))

    def discard_draft(self, request_id):
        self.commands.put(("discard", request_id))

    def _get_ocr(self):
        if self.ocr is None:
            self.ocr = WeChatOCR()
            self.signals.log.emit("系统", "本地 OCR 模型已加载")
        return self.ocr

    @property
    def api_key(self):
        return self.api_keys.get("primary", "")

    @api_key.setter
    def api_key(self, value):
        if value:
            self.api_keys["primary"] = value
        else:
            self.api_keys.pop("primary", None)

    def _provider_chain(self):
        slots = (
            ("primary", "主用", self.config.base_url, self.config.model),
            ("backup", "备用", self.config.backup_base_url, self.config.backup_model),
            ("fallback", "兜底", self.config.fallback_base_url, self.config.fallback_model),
        )
        chain = []
        for slot, label, url, model in slots:
            key = self.api_keys.get(slot, "").strip()
            url = (url or "").strip()
            model = (model or "").strip()
            if key and url and model:
                chain.append((slot, label, replace(self.config, base_url=url, model=model), key))
        return chain

    def _generate_with_failover(self, title, lines, images=None):
        chain = self._provider_chain()
        if not chain:
            raise LLMError("请先在设置中至少配置一个可用的大模型通道")
        failures = []
        for slot, label, config, api_key in chain:
            try:
                result = self.llm.generate_with_usage(
                    title, lines, config, api_key, images=images,
                )
            except Exception as error:
                failures.append(f"{label}通道（{type(error).__name__}）")
                self.signals.log.emit(
                    "错误", f"{title}：{label}通道请求失败（{type(error).__name__}）")
                continue
            text = (result.text or "").strip()
            if not text:
                failures.append(f"{label}通道（空回复）")
                self.signals.log.emit("错误", f"{title}：{label}通道返回了空回复")
                continue
            if slot != "primary":
                self.signals.log.emit("模型", f"{title}：已切换到{label}通道")
            return replace(result, text=text, model=result.model or config.model)
        raise LLMError("所有通道均失败：" + "，".join(failures))

    def _apply_config(self, config, api_keys):
        was_test_mode = self.config.test_mode
        self.config = config
        self.guard = ReplyGuard(config.reply_cooldown, config.hourly_limit)
        if config.test_mode and not was_test_mode:
            self._cancel_pending_sends(emit_drafts=False)
        if isinstance(api_keys, str):
            api_keys = {"primary": api_keys}
        self.api_keys = {str(slot): str(value) for slot, value in (api_keys or {}).items()
                         if value}
        inhibit_sleep = config.monitoring and config.keep_awake_when_monitoring
        if inhibit_sleep and not self._sleep_inhibited:
            if set_sleep_inhibition(True):
                self._sleep_inhibited = True
                self.signals.log.emit("系统", "监听期间已阻止系统睡眠，显示器仍可正常息屏")
            else:
                self.signals.log.emit("系统", "无法设置系统防睡眠，请检查 Windows 电源计划")
        elif self._sleep_inhibited and not inhibit_sleep:
            set_sleep_inhibition(False)
            self._sleep_inhibited = False
        auto_send_active = config.auto_send or config.test_mode
        if not auto_send_active or not config.monitoring:
            self._cancel_pending_sends()
        self.signals.connected.emit(bool(self.api_keys))
        if not self.api_keys:
            self.signals.status.emit("请先配置大模型 API Key")
        elif not config.monitoring:
            self.signals.status.emit("监听已暂停")
        elif config.test_mode:
            self.signals.status.emit("测试模式：仅处理文件传输助手并自动发送")
        else:
            self.signals.status.emit("API 已配置，等待新消息")

    def _cancel_pending_sends(self, emit_drafts=True):
        pending, self.pending_sends = self.pending_sends, []
        for _, request_id in pending:
            draft = self.drafts.get(request_id)
            if draft:
                self.signals.activity_resolved.emit(
                    request_id, "待审核" if emit_drafts else "已取消",
                )
                if emit_drafts:
                    self.signals.draft.emit(draft)
                else:
                    self.drafts.pop(request_id, None)

    def _random_reply_delay(self):
        if not self.config.random_reply_delay:
            return 0.0
        return round(random.uniform(
            self.config.random_delay_min, self.config.random_delay_max,
        ), 1)

    def _report_scan_error(self, error):
        message = str(error)
        if message == self._last_scan_error:
            self._scan_error_count += 1
        else:
            self._last_scan_error = message
            self._scan_error_count = 0
            self.signals.log.emit("错误", f"本轮扫描失败：{message}")
        self.signals.status.emit(f"本轮扫描失败：{message}")
        if self._scan_error_count in {5, 20}:
            self.signals.log.emit(
                "错误", f"同一扫描错误已重复 {self._scan_error_count + 1} 次，已降低重试频率")
        base = max(0.8, self.config.scan_interval)
        return min(10.0, base * (2 ** min(self._scan_error_count // 5, 3)))

    def _clear_scan_error(self):
        self._last_scan_error = ""
        self._scan_error_count = 0

    def _dispatch_pending_sends(self):
        if not self.config.monitoring:
            return
        now = time.monotonic()
        remaining = []
        for due, request_id in self.pending_sends:
            if due > now:
                remaining.append((due, request_id))
                continue
            draft = self.drafts.get(request_id)
            if draft:
                self._send(draft)
        self.pending_sends = remaining

    def _refresh_instances(self):
        try:
            instances = capture.list_instances()
            self.signals.instances.emit(instances)
            if not instances:
                self.signals.status.emit("未发现聊天窗口窗口，请先登录聊天窗口")
            return instances
        except Exception as error:
            self.signals.status.emit(f"聊天窗口扫描失败：{type(error).__name__}")
            return []

    def _snapshot(self, instance_id):
        instance = capture.resolve_instance(instance_id)
        frame, _ = capture.wait_for_frame(instance.hwnd)
        if frame is None:
            raise RuntimeError("聊天窗口画面抓取失败，请确认窗口未被完全遮挡")
        instance = capture.resolve_instance(instance_id)
        area = capture.locate_chat_area(frame)
        if area is None:
            raise RuntimeError("未找到聊天区域，请保持聊天窗口默认布局")
        return instance, frame, area

    def _valid_title(self, candidate, actual):
        if self.config.test_mode:
            return is_test_chat(candidate) and is_test_chat(actual)
        wanted, found = normalize_title(candidate), normalize_title(actual)
        return bool(wanted and found and (wanted in found or found in wanted))

    def _scan_once(self):
        if self.pending_sends:
            self.signals.status.emit("等待自动回复发送，暂停会话切换")
            return
        if not self.config.monitoring:
            self.signals.status.emit("监听已暂停")
            return
        if not self.instance_id:
            self.signals.status.emit("请选择一个聊天窗口")
            return
        if not self.api_keys:
            self.signals.status.emit("请先配置大模型 API Key")
            return
        instance, frame, area = self._snapshot(self.instance_id)
        ocr = self._get_ocr()
        badges = capture.find_unread_rows(frame, area.x0)
        title = ocr.title(frame, area)
        switched = False
        candidate_title = ""
        if self.config.test_mode:
            if not is_test_chat(title):
                if not badges:
                    self.signals.status.emit("测试模式：请向文件传输助手发送一条测试消息")
                    return
                rows = ocr.rows(frame, area.x0, badges)
                from .chat import match_unread_rows

                candidates = match_unread_rows(
                    badges, rows, title, area.x0, skip_system=False,
                    only_title=TEST_CHAT_TITLE,
                )
                if not candidates:
                    self.signals.status.emit("测试模式：未找到文件传输助手的未读消息")
                    return
                candidate = candidates[0]
                automation.click_row(instance.instance_id, candidate.y)
                instance, frame, area = self._snapshot(instance.instance_id)
                actual = ocr.title(frame, area)
                if not self._valid_title(candidate.title, actual):
                    self.signals.status.emit("测试模式：文件传输助手标题校验失败，本轮跳过")
                    return
                title, candidate_title, switched = actual, actual, True
                self.signals.log.emit("系统", f"已打开测试会话：{title}")
        elif self.config.auto_switch and badges:
            rows = ocr.rows(frame, area.x0, badges)
            from .chat import match_unread_rows

            candidates = match_unread_rows(
                badges, rows, title, area.x0, skip_system=self.config.skip_system)
            if not candidates:
                self.signals.status.emit("未读扫描：没有可确认的客户会话")
                return
            candidate = candidates[0]
            automation.click_row(instance.instance_id, candidate.y)
            instance, frame, area = self._snapshot(instance.instance_id)
            actual = ocr.title(frame, area)
            if not self._valid_title(candidate.title, actual):
                self.signals.status.emit("未读扫描：会话标题校验失败，本轮跳过")
                return
            title, candidate_title, switched = actual, actual, True
            self.signals.log.emit("系统", f"已打开未读会话：{title}")
        if not title:
            self.signals.status.emit("未识别到当前会话标题，本轮跳过")
            return
        self.current_title = title
        if (not self.config.test_mode
                and ((self.config.skip_system and is_system_chat(title))
                     or (not self.config.reply_groups and is_group_title(title)))):
            self.signals.status.emit(f"正在跳过系统或群会话：{title}")
            return
        lines = ocr.messages(frame, area)
        images = ocr.images(frame, area, lines) if self.config.understand_images else []
        if not lines and not images:
            self.signals.status.emit(f"{title}：暂未识别到消息")
            return
        digest = hashlib.sha1(frame[area.y_top:area.y_in].tobytes()).digest()
        memory = self.memory.get(title)
        first_visit = not memory.initialized
        if digest == self.message_hashes.get(title):
            self.signals.status.emit(f"监听中：{title}")
            return
        self.message_hashes[title] = digest
        fresh_lines = memory.observe(lines, allow_latest=switched)
        fresh_images = memory.observe_images(images, allow_latest=switched)
        events = [(line.y, line.text, None) for line in fresh_lines]
        events.extend(
            (image.y, f"[图片:{hashlib.sha1(image.data).hexdigest()[:12]}]", image)
            for image in fresh_images
        )
        if not events:
            if first_visit and not switched:
                self.signals.log.emit("系统", f"已建立会话基线：{title}")
            self.signals.status.emit(f"监听中：{title}")
            return
        _, source_text, _ = max(events, key=lambda event: event[0])
        self._generate_reply(instance.instance_id, title, source_text, lines, images[-3:])
        if candidate_title:
            self.signals.status.emit(f"已处理未读：{title}")
        else:
            self.signals.status.emit(f"监听中：{title}")

    def _generate_reply(self, instance_id, title, source_text, lines, images=None):
        decision = self.guard.check(
            title, source_text, reply_groups=self.config.reply_groups,
            skip_system=self.config.skip_system, test_mode=self.config.test_mode,
        )
        if not decision.allowed:
            self.signals.log.emit("安全", f"{title}：{decision.reason}")
            return
        try:
            result = self._generate_with_failover(title, lines, images=images)
        except Exception as error:
            self.signals.log.emit("错误", f"{title}：模型请求失败（{error}）")
            self.signals.status.emit("模型请求失败，未发送任何内容")
            return
        request_id = uuid.uuid4().hex
        draft = ReplyDraft(request_id, instance_id, title, result.text, source_text)
        self.drafts[request_id] = draft
        auto_send = self.config.auto_send or self.config.test_mode
        activity = ActivityRecord(
            request_id=request_id,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            title=title,
            reply=result.text[:4000],
            source_text=source_text[:1000],
            status="自动发送中" if auto_send else "待审核",
            auto_send=auto_send,
            had_image=bool(images),
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
            model=result.model,
        )
        self.signals.activity.emit(activity)
        self.signals.log.emit("模型", f"{title}：已生成待审核回复")
        if auto_send:
            delay = self._random_reply_delay()
            if delay > 0:
                self.pending_sends.append((time.monotonic() + delay, request_id))
                self.signals.log.emit("自动回复", f"{title}：随机等待 {delay:.1f} 秒")
                self.signals.status.emit(f"自动回复将在 {delay:.1f} 秒后发送：{title}")
            else:
                self._send(draft)
        else:
            self.signals.draft.emit(draft)
            self.signals.status.emit(f"有待审核回复：{title}")

    def _send(self, draft):
        chunks = split_reply_chunks(draft.text)
        next_index = 0
        try:
            decision = self.guard.check(
                draft.title, draft.source_text, reply_groups=self.config.reply_groups,
                skip_system=self.config.skip_system, test_mode=self.config.test_mode,
            )
            if not decision.allowed:
                raise RuntimeError(decision.reason)
            if not chunks:
                raise RuntimeError("回复内容为空")
            if len(chunks) > 1:
                self.signals.log.emit("发送", f"{draft.title}：将分 {len(chunks)} 条发送")
            for index, chunk in enumerate(chunks):
                instance, frame, area = self._snapshot(draft.instance_id)
                actual = self._get_ocr().title(frame, area)
                if not self._valid_title(draft.title, actual):
                    raise RuntimeError("会话已切换，为避免发错对象已停止")
                self.signals.status.emit(
                    f"正在分段发送 {index + 1}/{len(chunks)}：{draft.title}")
                automation.fill_reply(draft.instance_id, area, chunk)
                automation.send_filled_reply(draft.instance_id, area)
                next_index = index + 1
                if next_index < len(chunks):
                    gap = round(random.uniform(1.2, 3.6), 1)
                    time.sleep(gap)
            self.guard.mark_sent(draft.title, draft.source_text)
            self.drafts.pop(draft.request_id, None)
            self.signals.log.emit("发送", f"已发送给：{draft.title}")
            self.signals.sent.emit(draft.title, draft.text)
            self.signals.activity_resolved.emit(draft.request_id, "已发送")
            self.signals.draft_resolved.emit(draft.request_id, True)
        except Exception as error:
            self.signals.log.emit("发送", f"{draft.title}：发送失败（{error}）")
            remaining = chunks[next_index:] if chunks else [draft.text]
            if remaining:
                pending = replace(draft, text="\n".join(remaining))
                self.drafts[draft.request_id] = pending
                self.signals.status.emit(f"发送失败，剩余内容已转为待审核：{draft.title}")
                self.signals.draft.emit(pending)
            else:
                self.drafts.pop(draft.request_id, None)
            self.signals.activity_resolved.emit(draft.request_id, "发送失败")
            self.signals.draft_resolved.emit(draft.request_id, False)

    def _command(self, name, payload):
        if name == "configure":
            self._apply_config(*payload)
        elif name == "instance":
            self._cancel_pending_sends(emit_drafts=False)
            self.instance_id = payload
            self.current_title = ""
            self.message_hashes.clear()
            self.memory.clear()
            self.guard = ReplyGuard(self.config.reply_cooldown, self.config.hourly_limit)
            if payload:
                self.signals.log.emit("系统", "已绑定选中的聊天窗口")
        elif name == "instances":
            self._refresh_instances()
        elif name == "scan":
            try:
                self._scan_once()
                self._clear_scan_error()
            except Exception as error:
                self._report_scan_error(error)
        elif name == "fill":
            draft = self.drafts.get(payload)
            if not draft:
                return
            try:
                decision = self.guard.check(
                    draft.title, draft.source_text, reply_groups=self.config.reply_groups,
                    skip_system=self.config.skip_system, test_mode=self.config.test_mode,
                )
                if not decision.allowed:
                    raise RuntimeError(decision.reason)
                _, frame, area = self._snapshot(draft.instance_id)
                actual = self._get_ocr().title(frame, area)
                if not self._valid_title(draft.title, actual):
                    raise RuntimeError("会话已切换，为避免填错对象已停止")
                automation.fill_reply(draft.instance_id, area, draft.text)
                self.signals.log.emit("填入", f"已填入：{draft.title}")
            except Exception as error:
                self.signals.log.emit("填入", f"填入失败：{error}")
        elif name == "send":
            draft = self.drafts.get(payload)
            if draft:
                self._send(draft)
        elif name == "discard":
            if self.drafts.pop(payload, None):
                self.signals.log.emit("审核", "已丢弃一条待审核回复")
                self.signals.activity_resolved.emit(payload, "已丢弃")
        elif name == "test":
            chain = self._provider_chain()
            if not chain:
                self.signals.log.emit("连接", "请先填写至少一个通道的 Base URL、模型名称和 API Key")
                return
            from .models import MessageLine

            lines = [MessageLine("in", "请只回复：连接成功", 0)]
            for _, label, config, api_key in chain:
                try:
                    self.llm.generate("连接测试", lines, config, api_key)
                    self.signals.log.emit("连接", f"{label}通道（{config.model}）测试成功")
                except Exception as error:
                    self.signals.log.emit(
                        "连接", f"{label}通道（{config.model}）测试失败：{type(error).__name__}")

    def run(self):
        try:
            self._refresh_instances()
            next_instances = time.monotonic() + 2
            next_scan = 0
            while not self._stopped:
                try:
                    name, payload = self.commands.get(timeout=0.15)
                    if name == "stop":
                        break
                    self._command(name, payload)
                    if name in {"configure", "instance"}:
                        next_scan = 0
                except queue.Empty:
                    pass
                self._dispatch_pending_sends()
                now = time.monotonic()
                if self.config.monitoring and now >= next_instances:
                    self._refresh_instances()
                    next_instances = now + 2
                if self.config.monitoring and self.api_keys and self.instance_id and now >= next_scan:
                    try:
                        self._scan_once()
                        self._clear_scan_error()
                        delay = max(0.8, self.config.scan_interval)
                    except Exception as error:
                        delay = self._report_scan_error(error)
                    next_scan = time.monotonic() + delay
        finally:
            if self._sleep_inhibited:
                set_sleep_inhibition(False)
                self._sleep_inhibited = False
