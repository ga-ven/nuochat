import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from nuochat.training import MAX_TRAINING_CHARS, load_training_document


def _write_text_pdf(path):
    stream = b"BT /F1 12 Tf 20 100 Td (Course fee is 99 yuan.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n".encode()
    data += b"0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(data)


class TrainingDocumentTests(unittest.TestCase):
    def test_plain_text_and_gbk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "培训.txt"
            path.write_text("课程咨询先确认年级。", encoding="utf-8")
            self.assertEqual(load_training_document(path).text, "课程咨询先确认年级。")
            path.write_bytes("收费必须核实。".encode("gb18030"))
            self.assertEqual(load_training_document(path).text, "收费必须核实。")

    def test_docx_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "培训.docx"
            xml = (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>先问年级</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>不确定时转人工</w:t></w:r></w:p></w:body></w:document>'
            )
            with ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            self.assertEqual(load_training_document(path).text, "先问年级\n不确定时转人工")

    def test_pdf_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "培训.pdf"
            _write_text_pdf(path)
            self.assertIn("Course fee is 99 yuan.", load_training_document(path).text)

    def test_long_document_is_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "培训.md"
            path.write_text("培" * (MAX_TRAINING_CHARS + 100), encoding="utf-8")
            document = load_training_document(path)
            self.assertTrue(document.truncated)
            self.assertIn("已截取前", document.text)
            self.assertLess(len(document.text), MAX_TRAINING_CHARS + 100)

    def test_empty_and_unsupported_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_training_document(empty)
            unsupported = Path(directory) / "training.exe"
            unsupported.write_bytes(b"not a document")
            with self.assertRaises(ValueError):
                load_training_document(unsupported)


if __name__ == "__main__":
    unittest.main()
