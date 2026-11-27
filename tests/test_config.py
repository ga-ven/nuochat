import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from nuochat.config import (
    PROMPT_TEMPLATE_SUFFIX, PROMPT_TEMPLATES, AppConfig, load_config,
    load_prompt_template, save_config, save_prompt_template,
)
from nuochat.secrets import SecretStore


class ConfigTests(unittest.TestCase):
    def test_atomic_round_trip_and_no_secret_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            config = replace(
                AppConfig(), model="local-model", auto_send=False, understand_images=True,
                training_document_name="岗前培训.txt", training_document_text="收费必须核实。",
            )
            save_config(config, path)
            self.assertEqual(load_config(path), config)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("api_key", data)
            self.assertEqual(list(path.parent.glob("config.json.*")), [])

    def test_invalid_values(self):
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"base_url": "not-a-url"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"use_proxy": True, "proxy_url": ""})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"auto_send": "false"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"test_mode": "false"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"random_reply_delay": "false"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"random_delay_min": 20, "random_delay_max": 10})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"keep_awake_when_monitoring": "false"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"understand_images": "false"})
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"training_document_text": 123})
        self.assertEqual(AppConfig.from_dict({"hourly_limit": -5}).hourly_limit, 1)

    def test_builtin_tutor_template(self):
        prompt = PROMPT_TEMPLATES["荔枝家教客服小荔"]
        self.assertIn("客服小荔", prompt)
        self.assertIn("不编造价格", prompt)
        self.assertIn("转人工处理", prompt)

    def test_only_public_tutor_template_is_builtin(self):
        self.assertEqual(set(PROMPT_TEMPLATES), {"荔枝家教客服小荔"})

    def test_prompt_template_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "业务模板"
            target = save_prompt_template(path, "老师组客服", "先确认老师需求，再推进下一步。")
            self.assertTrue(target.name.endswith(PROMPT_TEMPLATE_SUFFIX))
            template = load_prompt_template(target)
            self.assertEqual(template.name, "老师组客服")
            self.assertEqual(template.prompt, "先确认老师需求，再推进下一步。")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"type", "version", "name", "prompt"})
            self.assertNotIn("api_key", data)

    def test_prompt_template_rejects_invalid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"invalid{PROMPT_TEMPLATE_SUFFIX}"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_prompt_template(path)
            path.write_text(json.dumps({"type": "other", "version": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_prompt_template(path)
            with self.assertRaises(ValueError):
                save_prompt_template(path, "空模板", "")

    def test_training_document_is_added_to_model_prompt(self):
        config = replace(
            AppConfig(), system_prompt="基础客服规则",
            training_document_name="培训.txt", training_document_text="课程收费以运营方报价为准。",
        )
        self.assertIn("基础客服规则", config.model_system_prompt)
        self.assertIn("岗前培训资料", config.model_system_prompt)
        self.assertIn("课程收费以运营方报价为准", config.model_system_prompt)

    def test_dpapi_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.bin"
            store = SecretStore(path)
            store.save("test-secret-value")
            self.assertNotIn(b"test-secret-value", path.read_bytes())
            self.assertEqual(store.load(), "test-secret-value")
            store.clear()
            self.assertEqual(store.load(), "")


if __name__ == "__main__":
    unittest.main()
