# NuoChat

<p align="center"><img src="assets/icon.png" width="128" height="128" alt="NuoChat 图标"></p>

NuoChat 是一个本地运行的原创桌面客服助手：在 Windows 上采集指定聊天窗口，通过本地 OCR 理解会话列表和消息，再调用你配置的 OpenAI 兼容大模型 API 生成回复。支持手动确认，也可以开启受限的自动回复。

全部功能均在本仓库内独立实现，不依赖其他桌面软件的代码或品牌资源。微信仅作为兼容平台说明。

## 下载

不想折腾 Python 环境的话，直接下载打包好的单文件版本：

**[NuoChat-v0.2.2-windows-x64.exe](https://github.com/ga-ven/nuochat/releases/latest/download/NuoChat-v0.2.2-windows-x64.exe)**（约 144 MB，Windows 10/11 x64）

首次运行会在 `%APPDATA%\NuoChat` 生成配置，API Key 通过 Windows DPAPI 加密存放在 `%LOCALAPPDATA%\NuoChat\secret.bin`，不会随仓库分发。


## 功能

- 只选择一个微信实例，使用 PID + 进程启动时间绑定，实例重启后拒绝静默串号。
- 本地 OCR 识别会话标题、未读红点和消息气泡，兼容微信深色主题，不把聊天截图写入磁盘。
- 可提取聊天图片并发送给支持视觉输入的模型；图片只在内存中编码，默认关闭。
- 可随时暂停或继续监听；暂停后停止扫描、自动切换和等待中的自动发送。
- 自动跳过“微信团队”“文件传输助手”等系统会话。
- 后台点击未读会话，不移动鼠标；填入回复和点击发送时保留用户当前前台窗口。
- OpenAI 兼容 `/chat/completions` API，可配置 Base URL、模型、系统提示词和超时。
- 内置“荔枝家教客服小荔”模板，可载入后继续编辑；支持导入、导出本地客服模板 JSON，业务提示词无需放进仓库。
- 支持在本机导入 TXT、Markdown、Word、PDF 岗前培训文档，自动纳入模型系统提示词。
- 默认手动确认；自动发送需要显式开启，并受冷却时间、每小时上限和去重限制。
- 自动发送支持可配置的随机等待区间，关闭自动发送时尚未发送的回复会转回人工审核。
- 提供默认关闭的测试模式：只处理真实会话“文件传输助手”并自动发送，用于验证 OCR、模型回复和实际发送链路。
- “设置”弹窗按模型通道、提示词与模板、频率与连接、安全说明分标签页展示；“频率与连接”支持一键恢复本页默认值。
- 主界面“工作概览”显示今日生成/发送量、待处理数、Token 消耗和最近回复对象与内容。
- 设置修改后自动保存到本地；API Key 使用 Windows DPAPI 加密保存，JSON 配置和日志不写入密钥。
- PySide6 桌面界面与原创图标，PyInstaller 打包为单个 `.exe`。

## 运行环境

- Windows 10/11 x64
- Python 3.10+（仅源码运行需要）
- 已登录的桌面版微信
- 一个 OpenAI 兼容 API 服务

## 源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
New-Item -ItemType Directory -Force "$env:APPDATA\NuoChat" | Out-Null
Copy-Item config.example.json "$env:APPDATA\NuoChat\config.json"
python -m nuochat
```

首次启动后：

1. 点击“刷新”，选择要处理的聊天窗口。
2. 点击右上角“设置”，在弹窗中填写 Base URL、模型和 API Key；家长或学生咨询可选择“荔枝家教客服小荔”，其他业务点击“导入客服模板”载入本地模板。需要时也可点击“导入岗前培训”选择 TXT、Markdown、Word 或 PDF 文档。
3. 勾选“启用监听与采集”，需要时再开启“自动处理未读消息”；窗口顶部可随时“暂停监听/继续监听”。
4. 如需理解图片，在“频率与连接”开启“理解聊天图片”，并使用支持图片输入的模型。
5. 新消息出现后查看建议，手动“填入聊天窗口”或“确认发送”。
6. 确认稳定后再考虑开启“自动发送”，并在“频率与连接”中设置随机等待范围。

## 打包 EXE

```powershell
.\build_exe.ps1
```

产物位于 `dist\NuoChat.exe`。首次构建需要安装 `requirements-dev.txt`。图标源文件为 `assets\icon.svg`，Windows 多尺寸图标为 `assets\icon.ico`。

## 配置与隐私

默认配置目录：

```text
%APPDATA%\NuoChat\config.json
%APPDATA%\NuoChat\stats.json
%LOCALAPPDATA%\NuoChat\secret.bin
```

可用环境变量 `NUOCHAT_CONFIG` 指定其他配置文件路径，统计文件会保存在同一目录。普通设置在修改后自动保存，关闭程序时也会再次保存；API Key 单独通过 Windows DPAPI 加密，不会出现在 `config.json`、日志或截图中。

`stats.json` 只保存在本机，记录最近 1000 条回复的对象、内容摘要、状态和模型返回的 Token 数，便于重启后继续查看工作量；其中可能包含聊天内容，请按聊天数据妥善保管。模型未返回 `usage` 时，Token 显示为 0 或“未返回”。

OCR 在本机执行；只有生成回复时，选中的会话上下文会发送到用户配置的 API 地址。开启图片理解后，最近 3 张聊天图片也会随请求发送；图片只在内存中编码，不写入磁盘。程序不会自带云端服务，也不会上传聊天记录到项目方。

## 安全默认值

- `auto_send = false`
- `test_mode = false`
- `skip_system = true`
- 每个会话默认冷却 8 秒
- 自动发送默认随机等待 8～20 秒
- 每小时默认最多 30 次回复
- 同一消息不会重复生成或发送
- 关闭采集后停止扫描和自动切换

随机间隔仅用于平滑发送节奏，不代表可以绕过平台限制；冷却、去重和每小时上限仍然生效。自动回复可能误判或生成不恰当内容，请先在小范围会话测试，遵守平台规则和当地法律法规。

## 息屏与睡眠

- 仅关闭显示器、系统没有睡眠或锁屏时，后台 OCR 和自动回复会继续运行。
- “监听时阻止系统睡眠（允许息屏）”默认开启，只阻止系统睡眠，不会让显示器保持点亮。
- 手动睡眠、休眠、锁屏或注销可能中断自动发送；这些操作恢复后请先检查运行日志和待审核回复。
- 该设置不能绕过 Windows 电源策略、组策略或硬件限制，也不能保证所有聊天窗口版本在锁屏后仍接受输入。

## 岗前培训文档

在“模型与提示词”页的客服模板旁点击“导入岗前培训”，支持 `.txt`、`.md`、`.docx` 和文字版 `.pdf`。文档只在本机解析，最多保留 20,000 字符，导入后自动保存并加入每次模型请求的系统提示词。

客服提示词可以通过“导出客服模板”保存为 `.nuochat-template.json`，文件只包含模板名称和提示词，不包含 API Key、培训文档或聊天记录；可以通过“导入客服模板”重新载入或分享给其他用户。业务模板建议保存在仓库外，`.gitignore` 已默认忽略该扩展名。使用独立模板且规则稳定时，可以不导入完整培训手册；系统仍会自动把当前客服提示词加入每次请求。完整手册适合本机留存用于培训、核对和更新模板；如果同时导入，手册会继续追加到每次请求，增加上下文和 Token 消耗，特殊收费表或易变规则仍应转人工确认。

培训文档正文会随当前会话一起发送到你配置的 API 地址，不会上传到项目方；请勿导入 API Key、身份证号、银行卡号等敏感信息。扫描版 PDF 需要先做 OCR 或转为 Word/文本。

## 开发

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions 会在 Windows 上运行测试并构建 EXE 产物。

## 目录

```text
nuochat/
  capture.py      # 微信实例、窗口采集、未读检测
  ocr.py          # RapidOCR 会话、消息和图片区域识别
  automation.py   # 后台点击、填入和发送
  config.py       # 非敏感配置
  secrets.py      # Windows DPAPI 密钥存储
  engine.py       # OpenAI 兼容 API
  safety.py       # 去重、限流和发送策略
  power.py        # 监听期间阻止系统睡眠
  training.py     # 岗前培训文档解析
  worker.py       # 后台采集线程
  app.py          # PySide6 界面
tests/
.github/workflows/
```

## 发布到 GitHub

仓库名使用 `nuochat`，已经包含 `.gitignore`、SECURITY.md 和 Windows Actions。确认没有 `config.json`、`secret.bin`、`.venv`、`dist` 或聊天截图后，可自行执行：

```powershell
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/<your-name>/nuochat.git
git push -u origin main
```

不要提交真实 API Key、个人配置或聊天数据。
