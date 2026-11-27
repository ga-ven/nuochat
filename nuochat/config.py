import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlparse


LIZHI_TUTOR_PROMPT = """你是“荔枝家教”的客服小荔，负责用自然、耐心的方式解答家长和学生的课程咨询。

【回复风格】
- 先回应对方的问题，再给出一个清晰的下一步，通常控制在 1～3 句。
- 语气亲切、口语化，不使用机械话术，不重复对方整句话。
- 一次只问 1～2 个最关键的问题，避免像填表一样连续盘问。

【需求了解】
首次咨询时，根据需要逐步了解：学生年级、辅导科目、当前困难或目标、所在区域、可上课时间、线上或线下偏好。信息足够后再推进，不强行索要无关隐私。

【信息边界】
- 课程、师资、收费、校区、排课和优惠只依据运营方提供的资料；不确定时明确说需要核实，并给出“我帮您确认后回复您”的下一步。
- 不编造价格、名额、教师背景、提分效果或成功案例，不承诺保过、保提分或录取结果。
- 不索要身份证号、银行卡号、支付验证码等敏感信息。
- 遇到投诉、退款、安全问题或超出权限的要求，先安抚并转人工处理。

【推进方式】
- 需求清楚后，确认是否方便试听或预约老师沟通。
- 预约前复述年级、科目、时间和授课方式，请对方确认。
- 结束时说明下一步由谁处理、预计何时回复，不做无法兑现的承诺。"""

PROMPT_TEMPLATES = {"荔枝家教客服小荔": LIZHI_TUTOR_PROMPT}

PROMPT_TEMPLATE_TYPE = "nuochat-prompt-template"
PROMPT_TEMPLATE_VERSION = 1
PROMPT_TEMPLATE_SUFFIX = ".nuochat-template.json"
MAX_PROMPT_TEMPLATE_BYTES = 1024 * 1024
MAX_PROMPT_TEMPLATE_CHARS = 50_000


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    prompt: str


def load_prompt_template(path):
    source = Path(path)
    try:
        if source.stat().st_size > MAX_PROMPT_TEMPLATE_BYTES:
            raise ValueError("客服模板文件不能超过 1 MB")
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("客服模板文件无法读取，请选择有效的 JSON 模板") from exc
    if (
        not isinstance(data, dict)
        or data.get("type") != PROMPT_TEMPLATE_TYPE
        or data.get("version") != PROMPT_TEMPLATE_VERSION
    ):
        raise ValueError("不是有效的 NuoChat 客服模板")
    name = data.get("name")
    prompt = data.get("prompt")
    if not isinstance(name, str) or not isinstance(prompt, str):
        raise ValueError("客服模板必须包含文本名称和提示词")
    name = name.strip()
    prompt = prompt.strip()
    if not name or len(name) > 100:
        raise ValueError("客服模板名称不能为空且不能超过 100 个字符")
    if not prompt:
        raise ValueError("客服模板提示词不能为空")
    if len(prompt) > MAX_PROMPT_TEMPLATE_CHARS:
        raise ValueError(f"客服模板提示词不能超过 {MAX_PROMPT_TEMPLATE_CHARS:,} 字符")
    return PromptTemplate(name, prompt)


def save_prompt_template(path, name, prompt):
    name = name.strip()
    prompt = prompt.strip()
    if not name or len(name) > 100:
        raise ValueError("客服模板名称不能为空且不能超过 100 个字符")
    if not prompt:
        raise ValueError("客服模板提示词不能为空")
    if len(prompt) > MAX_PROMPT_TEMPLATE_CHARS:
        raise ValueError(f"客服模板提示词不能超过 {MAX_PROMPT_TEMPLATE_CHARS:,} 字符")
    target = Path(path)
    if not target.name.endswith(PROMPT_TEMPLATE_SUFFIX):
        target = target.with_name(target.name + PROMPT_TEMPLATE_SUFFIX)
    payload = {
        "type": PROMPT_TEMPLATE_TYPE,
        "version": PROMPT_TEMPLATE_VERSION,
        "name": name,
        "prompt": prompt,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def default_config_path() -> Path:
    override = os.environ.get("NUOCHAT_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return root / "NuoChat" / "config.json"


@dataclass(frozen=True)
class AppConfig:
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    backup_base_url: str = ""
    backup_model: str = ""
    fallback_base_url: str = ""
    fallback_model: str = ""
    system_prompt: str = (
        "你是一个谨慎、友好的桌面聊天客服。只根据当前会话回复，语气自然简洁，"
        "不编造价格、承诺或事实。必要时先询问关键信息。"
    )
    training_document_name: str = ""
    training_document_text: str = ""
    temperature: float = 0.4
    monitoring: bool = True
    keep_awake_when_monitoring: bool = True
    auto_switch: bool = True
    understand_images: bool = False
    auto_send: bool = False
    test_mode: bool = False
    reply_groups: bool = True
    skip_system: bool = True
    scan_interval: float = 1.2
    reply_cooldown: float = 8.0
    random_reply_delay: bool = True
    random_delay_min: float = 8.0
    random_delay_max: float = 20.0
    hourly_limit: int = 30
    request_timeout: float = 45.0
    use_proxy: bool = False
    proxy_url: str = ""

    @classmethod
    def from_dict(cls, data):
        defaults = cls()
        for name in ("monitoring", "auto_switch", "understand_images", "auto_send",
                     "test_mode", "reply_groups", "skip_system", "random_reply_delay",
                     "use_proxy", "keep_awake_when_monitoring"):
            if name in data and not isinstance(data[name], bool):
                raise ValueError(f"{name} 必须是 true 或 false")
        for name in ("base_url", "model", "backup_base_url", "backup_model",
                     "fallback_base_url", "fallback_model", "system_prompt",
                     "training_document_name", "training_document_text", "proxy_url"):
            if name in data and not isinstance(data[name], str):
                raise ValueError(f"{name} 必须是文本")
        values = {}
        for name in defaults.__dataclass_fields__:
            if name in data:
                values[name] = data[name]
        config = replace(defaults, **values)
        config = replace(
            config,
            base_url=config.base_url.strip(),
            model=config.model.strip(),
            backup_base_url=config.backup_base_url.strip(),
            backup_model=config.backup_model.strip(),
            fallback_base_url=config.fallback_base_url.strip(),
            fallback_model=config.fallback_model.strip(),
            system_prompt=config.system_prompt.strip(),
            training_document_name=config.training_document_name.strip()[:255],
            training_document_text=config.training_document_text.strip(),
            proxy_url=config.proxy_url.strip(),
            temperature=min(2.0, max(0.0, float(config.temperature))),
            scan_interval=min(10.0, max(0.5, float(config.scan_interval))),
            reply_cooldown=min(300.0, max(0.0, float(config.reply_cooldown))),
            random_delay_min=min(300.0, max(0.0, float(config.random_delay_min))),
            random_delay_max=min(300.0, max(0.0, float(config.random_delay_max))),
            hourly_limit=min(1000, max(1, int(config.hourly_limit))),
            request_timeout=min(180.0, max(5.0, float(config.request_timeout))),
            monitoring=bool(config.monitoring),
            keep_awake_when_monitoring=bool(config.keep_awake_when_monitoring),
            auto_switch=bool(config.auto_switch),
            understand_images=bool(config.understand_images),
            auto_send=bool(config.auto_send),
            test_mode=bool(config.test_mode),
            reply_groups=bool(config.reply_groups),
            skip_system=bool(config.skip_system),
            random_reply_delay=bool(config.random_reply_delay),
            use_proxy=bool(config.use_proxy),
        )
        if config.random_delay_max < config.random_delay_min:
            raise ValueError("随机回复间隔的最大值不能小于最小值")
        parsed = urlparse(config.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须是完整的 http 或 https 地址")
        if not config.model:
            raise ValueError("模型名称不能为空")
        for label, url, model in (
            ("备用", config.backup_base_url, config.backup_model),
            ("兜底", config.fallback_base_url, config.fallback_model),
        ):
            if not url:
                continue
            optional = urlparse(url)
            if optional.scheme not in {"http", "https"} or not optional.netloc:
                raise ValueError(f"{label}通道的 Base URL 必须是完整的 http 或 https 地址")
            if not model:
                raise ValueError(f"{label}通道填写了 Base URL，还需填写模型名称")
        if config.use_proxy and not config.proxy_url:
            raise ValueError("启用代理时必须填写代理地址")
        return config

    @property
    def model_system_prompt(self):
        prompt = self.system_prompt.strip()
        training = self.training_document_text.strip()
        if training:
            prompt += (
                "\n\n【岗前培训资料】\n"
                "以下内容仅作为业务参考，不得覆盖安全边界；不确定的信息仍须核实或转人工。\n"
                f"{training}"
            )
        return prompt

    def to_dict(self):
        return asdict(self)


def load_config(path=None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppConfig()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取配置：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("配置格式错误")
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path=None):
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=config_path.name + ".", dir=config_path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
