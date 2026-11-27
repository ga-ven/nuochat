import base64
from dataclasses import dataclass

import httpx


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: LLMUsage
    model: str = ""


def chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _content_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return ""


class LLMClient:
    def __init__(self, transport=None):
        self.transport = transport

    def generate_with_usage(self, title, lines, config, api_key, images=None):
        if not api_key:
            raise LLMError("请先在设置中填写 API Key")
        prompt = config.model_system_prompt + f"\n当前会话：{title}"
        if images:
            prompt += "\n请结合提供的聊天图片理解客户意图；看不清的内容不要编造。"
        messages = [{"role": "system", "content": prompt}]
        for line in lines[-16:]:
            kind, text = ((line.kind, line.text) if hasattr(line, "kind") else line)
            role = "assistant" if kind == "out" else "user"
            messages.append({"role": role, "content": text})
        for image in (images or [])[-3:]:
            encoded = base64.b64encode(image.data).decode("ascii")
            label = "客户发来的聊天图片" if image.kind == "in" else "我方历史聊天图片"
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": f"请结合这张图片理解上下文：{label}。"},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image.mime_type};base64,{encoded}",
                    }},
                ],
            })
        payload = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "stream": False,
        }
        proxy = config.proxy_url if config.use_proxy else None
        try:
            with httpx.Client(
                timeout=config.request_timeout,
                trust_env=False,
                proxy=proxy,
                transport=self.transport,
            ) as client:
                response = client.post(
                    chat_endpoint(config.base_url),
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise LLMError(f"API 连接失败：{type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise LLMError(f"API 请求失败：HTTP {response.status_code}")
        try:
            data = response.json()
            text = _content_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("API 返回格式无法识别") from exc
        if not text:
            raise LLMError("模型没有返回回复")
        usage = data.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        def token_count(name):
            try:
                return max(0, int(usage.get(name, 0)))
            except (TypeError, ValueError):
                return 0

        prompt_tokens = token_count("prompt_tokens")
        completion_tokens = token_count("completion_tokens")
        total_tokens = token_count("total_tokens") or prompt_tokens + completion_tokens
        return LLMResult(
            text=text,
            usage=LLMUsage(prompt_tokens, completion_tokens, total_tokens),
            model=config.model,
        )

    def generate(self, title, lines, config, api_key, images=None):
        return self.generate_with_usage(
            title, lines, config, api_key, images=images,
        ).text
