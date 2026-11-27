import base64
import json
import unittest
from dataclasses import replace

import httpx

from nuochat.config import AppConfig
from nuochat.engine import LLMClient, chat_endpoint
from nuochat.models import ChatImage, MessageLine


class EngineTests(unittest.TestCase):
    def test_endpoint(self):
        self.assertEqual(chat_endpoint("https://example.test/v1/"),
                         "https://example.test/v1/chat/completions")
        self.assertEqual(chat_endpoint("https://example.test/v1/chat/completions"),
                         "https://example.test/v1/chat/completions")

    def test_request_and_response(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": [{"type": "text", "text": " 收到 "}]}}]
            })

        client = LLMClient(httpx.MockTransport(handler))
        config = replace(
            AppConfig(), base_url="https://example.test/v1", model="test-model",
            training_document_name="培训.txt", training_document_text="试听需先预约。",
        )
        lines = [MessageLine("in", "客户消息", 10), MessageLine("out", "历史回复", 20)]
        self.assertEqual(client.generate("客户甲", lines, config, "header-only-secret"), "收到")
        self.assertEqual(captured["authorization"], "Bearer header-only-secret")
        self.assertNotIn("header-only-secret", json.dumps(captured["body"], ensure_ascii=False))
        self.assertIn("试听需先预约", captured["body"]["messages"][0]["content"])
        self.assertEqual(captured["body"]["messages"][-1]["role"], "assistant")
        self.assertEqual(captured["body"]["model"], "test-model")

    def test_usage_is_returned_with_reply(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "收到"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            })

        client = LLMClient(httpx.MockTransport(handler))
        result = client.generate_with_usage(
            "客户甲", [], AppConfig(base_url="https://example.test/v1", model="test-model"),
            "secret",
        )
        self.assertEqual(result.text, "收到")
        self.assertEqual(result.usage.prompt_tokens, 120)
        self.assertEqual(result.usage.completion_tokens, 30)
        self.assertEqual(result.usage.total_tokens, 150)
        self.assertEqual(result.model, "test-model")

    def test_multimodal_request_contains_image_data_url(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "看到图片了"}}]
            })

        image = ChatImage("in", 20, b"image-bytes", "image/png")
        client = LLMClient(httpx.MockTransport(handler))
        reply = client.generate("客户甲", [MessageLine("in", "请看图", 10)],
                                AppConfig(model="vision-model"), "secret", images=[image])
        self.assertEqual(reply, "看到图片了")
        self.assertIn("结合提供的聊天图片", captured["body"]["messages"][0]["content"])
        content = captured["body"]["messages"][-1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        expected = "data:image/png;base64," + base64.b64encode(b"image-bytes").decode("ascii")
        self.assertEqual(content[1]["image_url"]["url"], expected)
        self.assertNotIn("secret", json.dumps(captured["body"]))

    def test_missing_key(self):
        with self.assertRaises(RuntimeError):
            LLMClient().generate("客户甲", [], AppConfig(), "")


if __name__ == "__main__":
    unittest.main()
