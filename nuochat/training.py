import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile


MAX_TRAINING_CHARS = 20_000
MAX_TRAINING_BYTES = 10 * 1024 * 1024
SUPPORTED_TRAINING_EXTENSIONS = (".txt", ".md", ".docx", ".pdf")


@dataclass(frozen=True)
class TrainingDocument:
    name: str
    text: str
    truncated: bool = False


def _decode_text(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本编码无法识别，请另存为 UTF-8 后重试")


def _docx_text(path):
    try:
        with ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("Word 文档无法读取，请使用标准 .docx 文件") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines = []
    for paragraph in root.iter(namespace + "p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == namespace + "t":
                parts.append(node.text or "")
            elif node.tag == namespace + "tab":
                parts.append("\t")
            elif node.tag in {namespace + "br", namespace + "cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("当前程序缺少 PDF 读取组件，请更新程序") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先解除密码保护")
        parts = [(page.extract_text() or "").strip() for page in reader.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF 无法读取，请使用文字版 PDF 或转为 Word/文本") from exc
    return "\n\n".join(part for part in parts if part)


def load_training_document(path):
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_TRAINING_EXTENSIONS:
        raise ValueError("仅支持 TXT、Markdown、DOCX 和 PDF 文件")
    size = source.stat().st_size
    if size == 0:
        raise ValueError("文档内容为空")
    if size > MAX_TRAINING_BYTES:
        raise ValueError("文档不能超过 10 MB")
    if suffix in {".txt", ".md"}:
        text = _decode_text(source.read_bytes())
    elif suffix == ".docx":
        text = _docx_text(source)
    else:
        text = _pdf_text(source)
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("未提取到文字；扫描版 PDF 请先做 OCR 或转为 Word/文本")
    truncated = len(text) > MAX_TRAINING_CHARS
    if truncated:
        text = text[:MAX_TRAINING_CHARS] + f"\n\n[文档过长，已截取前 {MAX_TRAINING_CHARS} 字符]"
    return TrainingDocument(source.name, text, truncated)
