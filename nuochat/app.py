import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QTimer
from PySide6.QtGui import QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSizePolicy, QSpinBox, QSplitter, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from . import __app_name__, __version__
from .chat import TEST_CHAT_TITLE
from .config import (
    MAX_PROMPT_TEMPLATE_CHARS, PROMPT_TEMPLATE_SUFFIX, PROMPT_TEMPLATES, AppConfig,
    default_config_path, load_config, load_prompt_template, save_config,
    save_prompt_template,
)
from .secrets import SecretStore
from .stats import PENDING_STATUSES, ActivityStore, default_stats_path
from .training import MAX_TRAINING_CHARS, load_training_document
from .worker import CopilotWorker


class CopilotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        icon = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
        self.setWindowIcon(QIcon(str(icon)) if icon.exists() else QIcon())
        self.setWindowTitle(f"{__app_name__} {__version__} · 桌面智能客服")
        self.setMinimumSize(920, 700)
        available = QApplication.primaryScreen().availableGeometry()
        width = max(self.minimumWidth(), min(1040, available.width() - 80))
        height = max(self.minimumHeight(), min(780, available.height() - 100))
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )
        self.instances = []
        self.drafts = {}
        self.api_keys = {}
        self.selected_instance_id = ""
        self.training_document_name = ""
        self.training_document_text = ""
        self.activity_store = ActivityStore()
        self.pending_activity_ids = set()
        self.settings_dialog = None
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(700)
        self.save_timer.timeout.connect(self._save_silently)
        self._loading = True
        self.worker = CopilotWorker()
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.signals.instances.connect(self._set_instances)
        self.worker.signals.status.connect(self._set_status)
        self.worker.signals.log.connect(self._append_log)
        self.worker.signals.draft.connect(self._add_draft)
        self.worker.signals.draft_resolved.connect(self._resolve_draft)
        self.worker.signals.activity.connect(self._record_activity)
        self.worker.signals.activity_resolved.connect(self._resolve_activity)
        self.worker.signals.sent.connect(lambda title, _: self._set_status(f"已发送：{title}"))
        self.worker.signals.connected.connect(self._set_connected)
        self._build_ui()
        self._load()
        self.thread.start()
        self.worker.configure(self._collect_config(), self.api_keys)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        if not self.windowIcon().isNull():
            logo = QLabel()
            logo.setPixmap(self.windowIcon().pixmap(46, 46))
            logo.setFixedSize(46, 46)
            header.addWidget(logo)
        title = QLabel(__app_name__)
        title.setObjectName("title")
        version = QLabel(f"v{__version__}")
        version.setObjectName("version")
        version.setToolTip(f"{__app_name__} {__version__}")
        subtitle = QLabel("本地 OCR  ·  大模型回复  ·  人工审核")
        subtitle.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(version, 0, Qt.AlignVCenter)
        header.addStretch()
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self._open_settings)
        header.addWidget(self.settings_button)
        header.addWidget(subtitle)
        layout.addLayout(header)

        self.status = QLabel("正在初始化…")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        top = QHBoxLayout()
        instance_box = QGroupBox("聊天窗口")
        instance_form = QVBoxLayout(instance_box)
        instance_row = QHBoxLayout()
        self.instance_combo = QComboBox()
        self.instance_combo.setMinimumWidth(300)
        self.refresh_button = QPushButton("刷新")
        self.scan_button = QPushButton("立即扫描")
        self.pause_button = QPushButton("暂停监听")
        instance_row.addWidget(self.instance_combo, 1)
        instance_row.addWidget(self.refresh_button)
        instance_row.addWidget(self.scan_button)
        instance_row.addWidget(self.pause_button)
        instance_form.addLayout(instance_row)
        hint = QLabel("只处理选中的聊天窗口。实例关闭或重启后会停止，不会自动改选其他账号。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        instance_form.addWidget(hint)
        top.addWidget(instance_box, 1)

        run_box = QGroupBox("运行策略")
        run_form = QGridLayout(run_box)
        run_form.setHorizontalSpacing(10)
        run_form.setVerticalSpacing(6)
        self.monitoring = QCheckBox("启用监听与采集")
        self.auto_switch = QCheckBox("自动处理未读消息")
        self.auto_send = QCheckBox("自动发送模型回复（高风险）")
        self.reply_groups = QCheckBox("允许回复群聊")
        self.skip_system = QCheckBox("跳过系统会话")
        self.monitoring.setChecked(True)
        self.auto_send.setChecked(False)
        self.reply_groups.setChecked(True)
        self.skip_system.setChecked(True)
        self.auto_send.stateChanged.connect(self._auto_send_changed)
        for index, checkbox in enumerate((self.monitoring, self.auto_switch,
                                          self.auto_send, self.reply_groups,
                                          self.skip_system)):
            run_form.addWidget(checkbox, index // 2, index % 2)
        top.addWidget(run_box)
        layout.addLayout(top)

        self.settings_pages = (
            ("模型通道", self._channels_tab()),
            ("提示词与模板", self._prompt_tab()),
            ("频率与连接", self._limits_tab()),
            ("安全说明", self._safety_tab()),
        )

        overview = QGroupBox("工作概览")
        overview_layout = QVBoxLayout(overview)
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        for key, label in (
            ("generated", "今日生成"),
            ("sent", "今日已发送"),
            ("pending", "待处理"),
            ("tokens", "今日 Token"),
        ):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(2)
            caption = QLabel(label)
            caption.setObjectName("statCaption")
            value = QLabel("0")
            value.setObjectName("statValue")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            stats_row.addWidget(card, 1)
            setattr(self, f"stat_{key}", value)
        overview_layout.addLayout(stats_row)
        self.stat_detail = QLabel()
        self.stat_detail.setObjectName("hint")
        self.stat_detail.setWordWrap(True)
        overview_layout.addWidget(self.stat_detail)
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(74)
        self.activity_list.setMaximumHeight(124)
        self.activity_list.setSpacing(2)
        self.activity_list.setToolTip("只显示最近 8 条生成、发送或审核记录")
        overview_layout.addWidget(self.activity_list)

        review = QGroupBox("待审核回复")
        review_layout = QHBoxLayout(review)
        left = QVBoxLayout()
        self.draft_list = QListWidget()
        self.draft_list.currentRowChanged.connect(self._show_draft)
        left.addWidget(self.draft_list)
        review_layout.addLayout(left, 1)
        right = QVBoxLayout()
        self.draft_preview = QPlainTextEdit()
        self.draft_preview.setReadOnly(True)
        self.draft_preview.setPlaceholderText("收到新消息后，模型回复会出现在这里。")
        right.addWidget(self.draft_preview, 1)
        buttons = QHBoxLayout()
        self.fill_button = QPushButton("填入聊天窗口")
        self.send_button = QPushButton("确认发送")
        self.discard_button = QPushButton("丢弃")
        self.fill_button.setEnabled(False)
        self.send_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.fill_button.clicked.connect(self._fill)
        self.send_button.clicked.connect(self._send)
        self.discard_button.clicked.connect(self._discard)
        buttons.addWidget(self.fill_button)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.discard_button)
        buttons.addStretch()
        right.addLayout(buttons)
        review_layout.addLayout(right, 2)

        logs = QGroupBox("运行日志")
        log_layout = QVBoxLayout(logs)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setMinimumHeight(90)
        log_layout.addWidget(self.log)

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.addWidget(overview)
        main_splitter.addWidget(review)
        main_splitter.addWidget(logs)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setStretchFactor(2, 1)
        main_splitter.setSizes([190, 220, 90])
        layout.addWidget(main_splitter, 1)
        self._refresh_overview()

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f3f6f4; color: #18231e; }
            QGroupBox { background: white; border: 1px solid #dce5df; border-radius: 8px;
                        margin-top: 10px; padding: 12px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #1e5b42; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #174c37; }
            QLabel#version { color: #4a6b5b; background: #e3efe8; border-radius: 9px;
                             padding: 2px 8px; font-size: 12px; font-weight: 600; }
            QLabel#subtitle, QLabel#hint { color: #63736b; }
            QLabel#status { background: #e3efe8; border-radius: 6px; padding: 9px;
                            color: #215a42; font-weight: 600; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
                background: #fbfdfb; border: 1px solid #cbd8d0; border-radius: 5px; padding: 5px; }
            QPushButton { background: #256c4e; color: white; border: 0; border-radius: 5px;
                          padding: 7px 16px; min-height: 22px; font-weight: 600; }
            QPushButton:hover { background: #1d5b41; }
            QPushButton:disabled { background: #aebdb4; }
            QFrame#statCard { background: #f4f9f6; border: 1px solid #d8e6dd; border-radius: 7px; }
            QLabel#statCaption { color: #63736b; font-size: 12px; }
            QLabel#statValue { color: #174c37; font-size: 22px; font-weight: 700; }
            QListWidget { background: #fbfdfb; border: 1px solid #dce5df; border-radius: 6px; }
            QListWidget::item { padding: 5px 7px; border-bottom: 1px solid #edf2ef; }
            QListWidget::item:selected { background: #e3efe8; color: #174c37; }
            QDialog { background: #f3f6f4; }
        """)

    def _channels_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(10, 10, 14, 10)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.model = QLineEdit()
        self.model.setPlaceholderText("gpt-4o-mini")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("留空表示保持已加密保存的密钥")
        self.backup_base_url = QLineEdit()
        self.backup_base_url.setPlaceholderText("可选，主用通道失败时自动切换")
        self.backup_model = QLineEdit()
        self.backup_model.setPlaceholderText("可选，例如 deepseek-chat")
        self.backup_key_input = QLineEdit()
        self.backup_key_input.setEchoMode(QLineEdit.Password)
        self.backup_key_input.setPlaceholderText("留空表示保持已加密保存的密钥")
        self.fallback_base_url = QLineEdit()
        self.fallback_base_url.setPlaceholderText("可选，备用也失败时的最后通道")
        self.fallback_model = QLineEdit()
        self.fallback_model.setPlaceholderText("可选，例如 qwen-plus")
        self.fallback_key_input = QLineEdit()
        self.fallback_key_input.setEchoMode(QLineEdit.Password)
        self.fallback_key_input.setPlaceholderText("留空表示保持已加密保存的密钥")
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.save_button = QPushButton("保存设置与密钥")
        self.test_button = QPushButton("测试连接")
        self.clear_key_button = QPushButton("清除密钥")
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.test_button)
        action_row.addWidget(self.clear_key_button)
        action_row.addStretch()
        failover_hint = QLabel(
            "按主用 → 备用 → 兜底顺序调用：某个通道报错、超时或返回空内容时自动切换下一个；"
            "未填写 Base URL 的通道会被跳过。"
        )
        failover_hint.setObjectName("hint")
        failover_hint.setWordWrap(True)
        form.addRow("主用 Base URL", self.base_url)
        form.addRow("主用模型", self.model)
        form.addRow("主用 API Key", self.api_key_input)
        form.addRow("备用 Base URL", self.backup_base_url)
        form.addRow("备用模型", self.backup_model)
        form.addRow("备用 API Key", self.backup_key_input)
        form.addRow("兜底 Base URL", self.fallback_base_url)
        form.addRow("兜底模型", self.fallback_model)
        form.addRow("兜底 API Key", self.fallback_key_input)
        form.addRow("", failover_hint)
        form.addRow("", action_row)
        self.save_button.clicked.connect(lambda: self._save())
        self.test_button.clicked.connect(self._test)
        self.clear_key_button.clicked.connect(self._clear_key)
        return widget

    def _prompt_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.prompt_template = QComboBox()
        self.prompt_template.addItem("自定义", "")
        for name, text in PROMPT_TEMPLATES.items():
            self.prompt_template.addItem(name, text)
        self.load_template_button = QPushButton("载入模板")
        self.load_template_button.setToolTip("载入后会替换当前客服提示词，可继续编辑")
        self.import_template_button = QPushButton("导入客服模板")
        self.import_template_button.setToolTip(
            f"导入本地客服模板 JSON，最多 {MAX_PROMPT_TEMPLATE_CHARS:,} 字符，不包含 API Key 或聊天记录"
        )
        self.export_template_button = QPushButton("导出客服模板")
        self.export_template_button.setToolTip("把当前客服提示词导出为本地 JSON，便于备份和分享")
        self.import_training_button = QPushButton("导入岗前培训")
        self.import_training_button.setToolTip("在本机读取 TXT、Markdown、Word 或 PDF，不会上传到其他网站")
        template_row = QHBoxLayout()
        template_row.setSpacing(8)
        template_row.addWidget(QLabel("客服模板"))
        template_row.addWidget(self.prompt_template, 1)
        template_row.addWidget(self.load_template_button)
        template_row.addWidget(self.import_template_button)
        template_row.addWidget(self.export_template_button)
        layout.addLayout(template_row)
        self.training_status = QLabel()
        self.training_status.setObjectName("hint")
        self.training_status.setWordWrap(True)
        self.remove_training_button = QPushButton("移除培训资料")
        self.remove_training_button.setEnabled(False)
        training_row = QHBoxLayout()
        training_row.setSpacing(8)
        training_row.addWidget(self.import_training_button)
        training_row.addWidget(self.training_status, 1)
        training_row.addWidget(self.remove_training_button)
        layout.addLayout(training_row)
        prompt_box = QGroupBox("客服提示词")
        prompt_layout = QVBoxLayout(prompt_box)
        prompt_layout.setContentsMargins(8, 8, 8, 8)
        self.system_prompt = QTextEdit()
        self.system_prompt.setMinimumHeight(160)
        self.system_prompt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.system_prompt.setPlainText(AppConfig().system_prompt)
        prompt_layout.addWidget(self.system_prompt)
        layout.addWidget(prompt_box, 1)
        self.load_template_button.clicked.connect(self._load_prompt_template)
        self.import_template_button.clicked.connect(self._import_prompt_template)
        self.export_template_button.clicked.connect(self._export_prompt_template)
        self.import_training_button.clicked.connect(self._import_training_document)
        self.remove_training_button.clicked.connect(self._remove_training_document)
        return widget

    def _limits_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.test_mode = QCheckBox(f"测试模式（仅{TEST_CHAT_TITLE}，自动发送）")
        self.test_mode.setToolTip("只处理真实会话“文件传输助手”，覆盖系统会话跳过规则并自动发送")
        test_hint = QLabel(
            f"开启后只识别、回复并发送到“{TEST_CHAT_TITLE}”，其他会话会被拦截。"
            "需启用监听与采集；可向文件传输助手发送一条文字消息验证完整链路。"
        )
        test_hint.setObjectName("hint")
        test_hint.setWordWrap(True)
        self.keep_awake = QCheckBox("监听时阻止系统睡眠（允许息屏）")
        self.keep_awake.setToolTip("只阻止系统睡眠，不会阻止显示器关闭；锁屏或手动睡眠仍可能中断")
        self.understand_images = QCheckBox("理解聊天图片（需要视觉模型）")
        self.understand_images.setToolTip("提取最近 3 张聊天图片并发送到当前配置的大模型通道")
        image_hint = QLabel(
            "开启后，聊天图片会与本机文字上下文一起发送到你配置的 API；"
            "请使用支持图片输入的模型。"
        )
        image_hint.setObjectName("hint")
        image_hint.setWordWrap(True)
        self.scan_interval = QDoubleSpinBox()
        self.scan_interval.setRange(0.8, 10.0)
        self.scan_interval.setSuffix(" 秒")
        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0, 300)
        self.cooldown.setSuffix(" 秒")
        self.random_delay_enabled = QCheckBox("随机回复间隔")
        self.random_delay_enabled.setToolTip("用于平滑自动回复节奏，仍需遵守平台规则")
        self.random_delay_min = QDoubleSpinBox()
        self.random_delay_min.setRange(0, 300)
        self.random_delay_min.setSuffix(" 秒")
        self.random_delay_max = QDoubleSpinBox()
        self.random_delay_max.setRange(0, 300)
        self.random_delay_max.setSuffix(" 秒")
        delay_row = QHBoxLayout()
        delay_row.addWidget(QLabel("最短"))
        delay_row.addWidget(self.random_delay_min, 1)
        delay_row.addWidget(QLabel("最长"))
        delay_row.addWidget(self.random_delay_max, 1)
        self.random_delay_min.valueChanged.connect(
            lambda value: self.random_delay_max.setValue(
                max(value, self.random_delay_max.value())))
        self.random_delay_max.valueChanged.connect(
            lambda value: self.random_delay_min.setValue(
                min(value, self.random_delay_min.value())))
        self.random_delay_enabled.stateChanged.connect(
            lambda state: self.random_delay_min.setEnabled(bool(state)))
        self.random_delay_enabled.stateChanged.connect(
            lambda state: self.random_delay_max.setEnabled(bool(state)))
        self.hourly_limit = QSpinBox()
        self.hourly_limit.setRange(1, 500)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(5, 180)
        self.timeout.setSuffix(" 秒")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.1)
        self.use_proxy = QCheckBox("使用显式代理")
        self.proxy_url = QLineEdit()
        self.proxy_url.setPlaceholderText("http://127.0.0.1:7890")
        self.proxy_url.setEnabled(False)
        self.use_proxy.stateChanged.connect(
            lambda state: self.proxy_url.setEnabled(bool(state)))
        form.addRow("调试测试", self.test_mode)
        form.addRow("", test_hint)
        form.addRow("电源管理", self.keep_awake)
        form.addRow("图片理解", self.understand_images)
        form.addRow("", image_hint)
        form.addRow("扫描间隔", self.scan_interval)
        form.addRow("同会话冷却", self.cooldown)
        form.addRow("自动发送节奏", self.random_delay_enabled)
        form.addRow("随机等待范围", delay_row)
        form.addRow("每小时发送上限", self.hourly_limit)
        form.addRow("请求超时", self.timeout)
        form.addRow("温度", self.temperature)
        form.addRow("", self.use_proxy)
        form.addRow("代理地址", self.proxy_url)
        reset_button = QPushButton("重置频率与连接")
        reset_button.setToolTip("恢复本页默认值，不影响模型通道、提示词或 API Key")
        reset_button.clicked.connect(self._reset_limits)
        form.addRow("", reset_button)
        return widget

    def _reset_limits(self):
        answer = QMessageBox.question(
            self, "重置频率与连接",
            "确定恢复本页默认值吗？\n\n将关闭测试模式并重置扫描、冷却、随机等待、发送上限、超时、温度、图片理解和代理，不影响模型通道、提示词或 API Key。",
        )
        if answer != QMessageBox.Yes:
            return
        defaults = AppConfig()
        self.test_mode.setChecked(defaults.test_mode)
        self.keep_awake.setChecked(defaults.keep_awake_when_monitoring)
        self.understand_images.setChecked(defaults.understand_images)
        self.scan_interval.setValue(defaults.scan_interval)
        self.cooldown.setValue(defaults.reply_cooldown)
        self.random_delay_enabled.setChecked(defaults.random_reply_delay)
        self.random_delay_min.setValue(defaults.random_delay_min)
        self.random_delay_max.setValue(defaults.random_delay_max)
        self.hourly_limit.setValue(defaults.hourly_limit)
        self.timeout.setValue(defaults.request_timeout)
        self.temperature.setValue(defaults.temperature)
        self.use_proxy.setChecked(defaults.use_proxy)
        self.proxy_url.setText(defaults.proxy_url)
        self._append_log("设置", "频率与连接已恢复默认值")
        self._schedule_save()

    def _safety_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "1. 默认关闭自动发送。模型生成内容后必须由你审核，再选择填入或发送。\n\n"
            "2. 自动处理未读只会在你选中的聊天窗口中切换会话，系统会话默认跳过。\n\n"
            "3. 每次发送前都会重新确认聊天窗口与会话标题；标题变化时停止发送。\n\n"
            "4. API Key 使用 Windows DPAPI 加密保存在本机，不写入配置文件或日志。\n\n"
            "5. OCR 在本机运行，但最近 16 条可见消息和你的提示词会发送到你配置的大模型 API；"
            "开启图片理解时，最近 3 张聊天图片也会发送。\n\n"
            "6. 随机回复间隔只用于平滑发送节奏，不代表可以绕过平台限制；冷却、去重和每小时上限仍然生效。\n\n"
            "7. 请仅用于你有权管理的聊天账号。发送前确认回复内容，遵守平台规则和当地法律。\n\n"
            "8. 岗前培训文档内容会随每次模型请求发送到你配置的 API，请勿导入密钥、身份证号等敏感信息。\n\n"
            "9. 测试模式只允许“文件传输助手”，会自动发送回复；其他会话会在扫描、生成、填入和发送阶段被拦截。"
        )
        layout.addWidget(text)
        return widget

    def _open_settings(self):
        if self.settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("设置")
            dialog.setModal(False)
            dialog.resize(780, 620)
            layout = QVBoxLayout(dialog)
            tabs = QTabWidget()
            for title, page in self.settings_pages:
                tabs.addTab(page, title)
            layout.addWidget(tabs, 1)
            self.save_status = QLabel("设置修改后会自动保存；API Key 只保存到本机加密文件。")
            self.save_status.setObjectName("hint")
            self.save_status.setWordWrap(True)
            layout.addWidget(self.save_status)
            paths = QLabel(
                f"配置文件：{default_config_path()}\n"
                f"统计文件：{default_stats_path()}"
            )
            paths.setObjectName("hint")
            paths.setWordWrap(True)
            layout.addWidget(paths)
            footer = QHBoxLayout()
            footer.addStretch()
            close_button = QPushButton("关闭")
            close_button.clicked.connect(dialog.close)
            footer.addWidget(close_button)
            layout.addLayout(footer)
            dialog.finished.connect(lambda: self._save_silently())
            self.settings_dialog = dialog
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _refresh_overview(self):
        summary = self.activity_store.summary()
        self.stat_generated.setText(f"{summary.today_generated:,}")
        self.stat_sent.setText(f"{summary.today_sent:,}")
        self.stat_pending.setText(f"{len(self.pending_activity_ids):,}")
        self.stat_tokens.setText(f"{summary.today_total_tokens:,}")
        self.stat_detail.setText(
            f"今日自动发送 {summary.today_auto_sent} · 人工确认 {summary.today_manual_sent} · "
            f"输入 Token {summary.today_prompt_tokens:,} · 输出 Token {summary.today_completion_tokens:,} · "
            f"累计发送 {summary.total_sent:,} · 累计 Token {summary.total_tokens:,}"
        )
        self.activity_list.clear()
        records = self.activity_store.recent(8)
        if not records:
            self.activity_list.addItem("暂无回复记录。生成回复后会在这里显示对象、内容和 Token。")
            return
        for record in records:
            try:
                stamp = datetime.fromisoformat(record.timestamp).strftime("%m-%d %H:%M")
            except ValueError:
                stamp = record.timestamp
            token_text = (
                f"{record.total_tokens:,} Token" if record.total_tokens else "Token 未返回"
            )
            image_text = " · 图片" if record.had_image else ""
            preview = " ".join(record.reply.split())[:90]
            item = QListWidgetItem(
                f"{stamp}  {record.title} · {record.status} · {token_text}{image_text}\n{preview}"
            )
            item.setToolTip(f"{record.title}\n{record.reply}\n\n模型：{record.model or '未记录'}")
            self.activity_list.addItem(item)

    def _record_activity(self, record):
        if record.status in PENDING_STATUSES:
            self.pending_activity_ids.add(record.request_id)
        else:
            self.pending_activity_ids.discard(record.request_id)
        try:
            self.activity_store.add(record)
        except Exception as error:
            self._append_log("统计", f"活动记录保存失败：{type(error).__name__}")
        self._refresh_overview()

    def _resolve_activity(self, request_id, status):
        if status in PENDING_STATUSES:
            self.pending_activity_ids.add(request_id)
        else:
            self.pending_activity_ids.discard(request_id)
        try:
            self.activity_store.resolve(request_id, status)
        except Exception as error:
            self._append_log("统计", f"活动状态保存失败：{type(error).__name__}")
        self._refresh_overview()

    def _load(self):
        try:
            config = load_config()
        except Exception as error:
            config = AppConfig()
            self._append_log("配置", f"配置读取失败，已使用默认值：{type(error).__name__}")
        self.base_url.setText(config.base_url)
        self.model.setText(config.model)
        self.backup_base_url.setText(config.backup_base_url)
        self.backup_model.setText(config.backup_model)
        self.fallback_base_url.setText(config.fallback_base_url)
        self.fallback_model.setText(config.fallback_model)
        self.system_prompt.setPlainText(config.system_prompt)
        self.training_document_name = config.training_document_name
        self.training_document_text = config.training_document_text
        self._update_training_status()
        self.monitoring.setChecked(config.monitoring)
        self.keep_awake.setChecked(config.keep_awake_when_monitoring)
        self.auto_switch.setChecked(config.auto_switch)
        self.understand_images.setChecked(config.understand_images)
        self.auto_send.setChecked(config.auto_send)
        self.test_mode.setChecked(config.test_mode)
        self.reply_groups.setChecked(config.reply_groups)
        self.skip_system.setChecked(config.skip_system)
        self.scan_interval.setValue(config.scan_interval)
        self.cooldown.setValue(config.reply_cooldown)
        self.random_delay_min.setValue(config.random_delay_min)
        self.random_delay_max.setValue(config.random_delay_max)
        self.random_delay_enabled.setChecked(config.random_reply_delay)
        self.hourly_limit.setValue(config.hourly_limit)
        self.timeout.setValue(config.request_timeout)
        self.temperature.setValue(config.temperature)
        self.use_proxy.setChecked(config.use_proxy)
        self.proxy_url.setText(config.proxy_url)
        try:
            self.api_keys = SecretStore().load_all()
        except Exception as error:
            self.api_keys = {}
            self._append_log("密钥", f"本机密钥读取失败：{type(error).__name__}")
        self.api_key_input.clear()
        self.backup_key_input.clear()
        self.fallback_key_input.clear()
        self.refresh_button.clicked.connect(self._refresh)
        self.scan_button.clicked.connect(self.worker.request_scan)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.monitoring.stateChanged.connect(self._monitoring_changed)
        self.instance_combo.currentIndexChanged.connect(self._instance_selected)
        self._update_pause_button()
        self._append_log("系统", f"配置文件：{default_config_path()}")
        self._loading = False
        self._connect_autosave()

    def _connect_autosave(self):
        def schedule(*_):
            self._schedule_save()

        for field in (
            self.base_url, self.model, self.backup_base_url, self.backup_model,
            self.fallback_base_url, self.fallback_model, self.proxy_url,
        ):
            field.textChanged.connect(schedule)
        for field in (self.api_key_input, self.backup_key_input, self.fallback_key_input):
            field.editingFinished.connect(self._save_silently)
        self.system_prompt.textChanged.connect(schedule)
        for spin in (
            self.scan_interval, self.cooldown, self.random_delay_min,
            self.random_delay_max, self.hourly_limit, self.timeout, self.temperature,
        ):
            spin.valueChanged.connect(schedule)
        for checkbox in (
            self.keep_awake, self.understand_images, self.auto_switch, self.test_mode,
            self.reply_groups, self.skip_system, self.random_delay_enabled, self.use_proxy,
        ):
            checkbox.stateChanged.connect(schedule)

    def _schedule_save(self):
        if not self._loading:
            self.save_timer.start()

    def _save_silently(self):
        self._save(silent=True)

    def _collect_config(self):
        return AppConfig(
            base_url=self.base_url.text().strip() or AppConfig().base_url,
            model=self.model.text().strip() or AppConfig().model,
            backup_base_url=self.backup_base_url.text().strip(),
            backup_model=self.backup_model.text().strip(),
            fallback_base_url=self.fallback_base_url.text().strip(),
            fallback_model=self.fallback_model.text().strip(),
            system_prompt=self.system_prompt.toPlainText().strip() or AppConfig().system_prompt,
            training_document_name=self.training_document_name,
            training_document_text=self.training_document_text,
            temperature=self.temperature.value(),
            monitoring=self.monitoring.isChecked(),
            keep_awake_when_monitoring=self.keep_awake.isChecked(),
            auto_switch=self.auto_switch.isChecked(),
            understand_images=self.understand_images.isChecked(),
            auto_send=self.auto_send.isChecked(),
            test_mode=self.test_mode.isChecked(),
            reply_groups=self.reply_groups.isChecked(),
            skip_system=self.skip_system.isChecked(),
            scan_interval=self.scan_interval.value(),
            reply_cooldown=self.cooldown.value(),
            random_reply_delay=self.random_delay_enabled.isChecked(),
            random_delay_min=self.random_delay_min.value(),
            random_delay_max=self.random_delay_max.value(),
            hourly_limit=self.hourly_limit.value(),
            request_timeout=self.timeout.value(),
            use_proxy=self.use_proxy.isChecked(),
            proxy_url=self.proxy_url.text().strip() if self.use_proxy.isChecked() else "",
        )

    def _load_prompt_template(self):
        text = self.prompt_template.currentData()
        if not text:
            return
        self.system_prompt.setPlainText(text)
        self._append_log("模板", f"已载入：{self.prompt_template.currentText()}")

    def _import_prompt_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入客服模板", "", f"客服模板 (*{PROMPT_TEMPLATE_SUFFIX})",
        )
        if not path:
            return
        try:
            template = load_prompt_template(path)
        except Exception as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        index = self.prompt_template.findText(template.name)
        if index < 0:
            self.prompt_template.addItem(template.name, template.prompt)
            index = self.prompt_template.count() - 1
        else:
            self.prompt_template.setItemData(index, template.prompt)
        self.prompt_template.setCurrentIndex(index)
        self.system_prompt.setPlainText(template.prompt)
        self._append_log("模板", f"已导入：{template.name}")
        self._save()

    def _export_prompt_template(self):
        prompt = self.system_prompt.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "导出失败", "客服提示词不能为空")
            return
        name = self.prompt_template.currentText().strip()
        if not name or name == "自定义":
            name = "自定义客服模板"
        invalid_chars = '\\/:*?"<>|'
        filename = "".join("_" if char in invalid_chars else char for char in name).strip()
        default_path = str(Path.home() / f"{filename or '客服模板'}{PROMPT_TEMPLATE_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出客服模板", default_path, f"客服模板 (*{PROMPT_TEMPLATE_SUFFIX})",
        )
        if not path:
            return
        try:
            target = save_prompt_template(path, name, prompt)
        except Exception as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self._append_log("模板", f"已导出：{target.name}")

    def _update_training_status(self):
        if self.training_document_name and self.training_document_text:
            self.training_status.setText(
                f"已导入：{self.training_document_name} · {len(self.training_document_text):,} 字符"
            )
        else:
            self.training_status.setText(
                f"未导入 · 支持 TXT / Markdown / Word / PDF，最多 {MAX_TRAINING_CHARS:,} 字符"
            )
        self.remove_training_button.setEnabled(bool(self.training_document_text))

    def _import_training_document(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择岗前培训文档", "", "培训文档 (*.txt *.md *.docx *.pdf)",
        )
        if not path:
            return
        try:
            document = load_training_document(path)
        except Exception as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        self.training_document_name = document.name
        self.training_document_text = document.text
        self._update_training_status()
        self._append_log("培训", f"已导入：{document.name} · {len(document.text):,} 字符")
        if document.truncated:
            QMessageBox.information(
                self, "文档已截断", f"文档超过 {MAX_TRAINING_CHARS:,} 字符，已保留前 {MAX_TRAINING_CHARS:,} 字符。",
            )
        self._save()

    def _remove_training_document(self):
        self.training_document_name = ""
        self.training_document_text = ""
        self._update_training_status()
        self._append_log("培训", "已移除岗前培训文档")
        self._save()

    def _save(self, silent=False):
        try:
            key_updates = {}
            for slot, field in (("primary", self.api_key_input),
                                ("backup", self.backup_key_input),
                                ("fallback", self.fallback_key_input)):
                entered = field.text().strip()
                if entered:
                    key_updates[slot] = entered
            api_keys = dict(self.api_keys)
            api_keys.update(key_updates)
            SecretStore().save_all(api_keys)
            self.api_keys = api_keys
            for field in (self.api_key_input, self.backup_key_input, self.fallback_key_input):
                field.clear()
            config = AppConfig.from_dict(self._collect_config().to_dict())
            save_config(config)
            self.worker.configure(config, self.api_keys)
            self._set_connected(bool(self.api_keys))
            saved_at = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status"):
                self.save_status.setText(f"已保存到本地 · {saved_at}")
            if not silent:
                self._append_log("配置", "设置已保存，密钥已通过 DPAPI 加密存储")
        except Exception as error:
            message = f"设置保存失败：{error}"
            self._append_log("配置", message)
            if hasattr(self, "save_status"):
                self.save_status.setText(message)
            if not silent:
                QMessageBox.warning(self, "保存失败", message)

    def _test(self):
        self.worker.configure(self._collect_config(), self.api_keys)
        self.worker.test_connection()

    def _clear_key(self):
        answer = QMessageBox.question(self, "清除密钥", "确定清除本机加密保存的全部 API Key 吗？")
        if answer != QMessageBox.Yes:
            return
        try:
            SecretStore().clear()
            self.api_keys = {}
            self.api_key_input.clear()
            self.backup_key_input.clear()
            self.fallback_key_input.clear()
            self.worker.configure(self._collect_config(), self.api_keys)
            self._set_connected(False)
            self._append_log("密钥", "本机 API Key 已全部清除")
        except Exception as error:
            self._append_log("密钥", f"清除失败：{type(error).__name__}")

    def _auto_send_changed(self, state):
        if state != Qt.Checked:
            return
        answer = QMessageBox.warning(
            self, "确认开启自动发送",
            "开启后，模型回复会直接发送，不再经过人工审核。请仅在你信任模型输出并已配置频率限制时开启。\n\n确定开启吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.auto_send.setChecked(False)
        else:
            self._schedule_save()

    def _refresh(self):
        self.worker.refresh_instances()

    def _update_pause_button(self):
        self.pause_button.setText("继续监听" if not self.monitoring.isChecked() else "暂停监听")

    def _toggle_pause(self):
        self.monitoring.setChecked(not self.monitoring.isChecked())

    def _monitoring_changed(self):
        self._update_pause_button()
        self.save_timer.stop()
        self.worker.configure(self._collect_config(), self.api_keys)
        self._save(silent=True)
        self._append_log("系统", "监听已暂停" if not self.monitoring.isChecked() else "监听已继续")

    def _set_instances(self, instances):
        self.instances = instances
        current = self.instance_combo.currentData()
        blocked = self.instance_combo.blockSignals(True)
        self.instance_combo.clear()
        for instance in instances:
            state = "  ·  已最小化" if instance.left <= -30000 else ""
            self.instance_combo.addItem(
                f"PID {instance.pid}  ·  {instance.title}  ·  {instance.width}×{instance.height}{state}",
                instance.instance_id,
            )
        if current:
            index = self.instance_combo.findData(current)
            if index >= 0:
                self.instance_combo.setCurrentIndex(index)
        elif len(instances) == 1:
            self.instance_combo.setCurrentIndex(0)
        self.instance_combo.blockSignals(blocked)
        selected = self.instance_combo.currentData() or ""
        if current and self.instance_combo.findData(current) < 0:
            selected = ""
            self._set_status("选中的聊天窗口已关闭或重启，请重新选择")
        if selected != self.selected_instance_id:
            self.selected_instance_id = selected
            self.worker.select_instance(selected)

    def _instance_selected(self, index):
        selected = self.instance_combo.itemData(index) if index >= 0 else ""
        if selected != self.selected_instance_id:
            self.selected_instance_id = selected
            self.worker.select_instance(selected)

    def _set_status(self, text):
        self.status.setText(text)

    def _set_connected(self, connected):
        self.status.setStyleSheet(
            "background: #dcefe4; color: #215a42;" if connected
            else "background: #f7ead7; color: #805b21;"
        )

    def _append_log(self, category, message):
        safe = message
        for key in self.api_keys.values():
            if key:
                safe = safe.replace(key, "***")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] [{category}] {safe}")
        self.log.moveCursor(QTextCursor.End)

    def _add_draft(self, draft):
        self.drafts[draft.request_id] = draft
        item = None
        for row in range(self.draft_list.count()):
            candidate = self.draft_list.item(row)
            if candidate.data(Qt.UserRole) == draft.request_id:
                item = candidate
                break
        if item is None:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, draft.request_id)
            self.draft_list.addItem(item)
        item.setText(f"{draft.title}\n{draft.text[:70]}")
        self.draft_list.setCurrentItem(item)
        self._show_draft()

    def _selected_draft(self):
        item = self.draft_list.currentItem()
        return self.drafts.get(item.data(Qt.UserRole)) if item else None

    def _show_draft(self):
        draft = self._selected_draft()
        self.draft_preview.setPlainText(draft.text if draft else "")
        enabled = draft is not None
        self.fill_button.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.discard_button.setEnabled(enabled)

    def _resolve_draft(self, request_id, success):
        if success:
            for row in range(self.draft_list.count()):
                item = self.draft_list.item(row)
                if item.data(Qt.UserRole) == request_id:
                    self.draft_list.takeItem(row)
                    break
            self.drafts.pop(request_id, None)
        self._show_draft()

    def _fill(self):
        draft = self._selected_draft()
        if draft:
            self.worker.fill_draft(draft.request_id)

    def _send(self):
        draft = self._selected_draft()
        if not draft:
            return
        answer = QMessageBox.question(self, "确认发送", f"确定把这条回复发送给「{draft.title}」吗？")
        if answer == QMessageBox.Yes:
            self.worker.send_draft(draft.request_id)

    def _discard(self):
        draft = self._selected_draft()
        if draft:
            self.worker.discard_draft(draft.request_id)
            self._resolve_draft(draft.request_id, True)

    def closeEvent(self, event):
        self.save_timer.stop()
        self._save(silent=True)
        self.worker.stop()
        self.thread.quit()
        if not self.thread.wait(8000):
            self.thread.terminate()
            self.thread.wait(1000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    window = CopilotWindow()
    app.setWindowIcon(window.windowIcon())
    window.show()
    raise SystemExit(app.exec())
