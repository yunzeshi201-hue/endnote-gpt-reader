"""Synthetic functional checks; never touch a real EndNote library."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import endnote_reader as reader


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="endnote-reader-test-")
        self.root = Path(self.temporary.name)
        self.pdf_root = self.root / "书库.Data" / "PDF"
        self.pdf_root.mkdir(parents=True)
        self.xml = self.root / "library.xml"

    def tearDown(self):
        self.temporary.cleanup()

    def write_xml(self, records, namespace=False):
        ns = ' xmlns="urn:synthetic-endnote"' if namespace else ""
        self.xml.write_text(f"<?xml version='1.0' encoding='UTF-8'?><xml{ns}><records>{records}</records></xml>", encoding="utf-8")

    def record_xml(self, number="42", links=None):
        links = links or []
        urls = "".join(f"<url>{url}</url>" for url in links)
        return f"""<record><rec-number>{number}</rec-number>
        <titles><title>研究 <style face="italic">Ultrafast</style> Dynamics</title>
        <secondary-title>Journal of Synthetic Tests</secondary-title></titles>
        <contributors><authors><author>张, 三</author><author>Doe, Jane</author></authors></contributors>
        <dates><year>2026</year></dates><electronic-resource-num>10.1234/demo</electronic-resource-num>
        <abstract>A test of charge transfer.</abstract><keywords><keyword>光谱</keyword></keywords>
        <urls><pdf-urls>{urls}</pdf-urls><web-urls><url>https://doi.org/10.1234/demo</url></web-urls></urls></record>"""

    def test_namespaced_mixed_content_and_search(self):
        self.write_xml(self.record_xml(), namespace=True)
        records = reader.load_records(self.xml, self.pdf_root)
        self.assertEqual(records[0]["title"], "研究 Ultrafast Dynamics")
        for term in ("张", "10.1234/demo", "charge transfer", "光谱", "ultrafast"):
            self.assertEqual(reader.search_records(records, term, 10)["total_matches"], 1)
        self.assertEqual(records[0]["attachments"], [])
        self.assertEqual(records[0]["fulltext_status"], "metadata_only")

    def test_unicode_url_decoding_and_missing_pdf(self):
        folder = self.pdf_root / "123"
        folder.mkdir()
        expected = folder / "测试 文献.pdf"
        expected.write_bytes(b"synthetic placeholder; metadata commands do not parse PDFs")
        self.write_xml(self.record_xml(links=["internal-pdf://123/%E6%B5%8B%E8%AF%95%20%E6%96%87%E7%8C%AE.pdf", "internal-pdf://missing.pdf"]))
        records = reader.load_records(self.xml, self.pdf_root)
        self.assertEqual(records[0]["attachments"][0]["path"], str(expected.resolve()))
        self.assertTrue(records[0]["attachments"][0]["available"])
        self.assertEqual(records[0]["attachments"][1]["reason"], "file_not_found")

    def test_traversal_and_absolute_internal_link_are_rejected(self):
        for raw in ("internal-pdf://../escape.pdf", "internal-pdf://%2e%2e%2fescape.pdf", "internal-pdf://..\\escape.pdf", "internal-pdf://C:/outside.pdf"):
            attachment = reader.resolve_attachment(raw, self.pdf_root)
            self.assertFalse(attachment["available"])
            self.assertIsNone(attachment["path"])

    def test_file_uri_and_absolute_path(self):
        path = self.pdf_root / "paper 论文.pdf"
        path.write_bytes(b"placeholder")
        for raw in (str(path), path.as_uri()):
            attachment = reader.resolve_attachment(raw, None)
            self.assertTrue(attachment["available"], attachment)
            self.assertEqual(Path(attachment["path"]), path.resolve())

    def test_attachment_filename_preserves_repeated_spaces(self):
        path = self.pdf_root / "paper  double space.pdf"
        path.write_bytes(b"placeholder")
        self.write_xml(self.record_xml(links=["internal-pdf://paper  double space.pdf"]))
        record = reader.load_records(self.xml, self.pdf_root)[0]
        self.assertTrue(record["attachments"][0]["available"])
        self.assertEqual(Path(record["attachments"][0]["path"]), path.resolve())

    def test_unrelated_xml_is_not_reported_as_empty_library(self):
        self.xml.write_text("<document><title>Not EndNote</title></document>", encoding="utf-8")
        with self.assertRaisesRegex(reader.ReaderError, "records container"):
            reader.load_records(self.xml, self.pdf_root)

    def test_remote_and_non_pdf_links_rejected(self):
        self.assertEqual(reader.resolve_attachment("https://example.org/paper.pdf", self.pdf_root)["reason"], "remote_or_unsupported_scheme")
        self.assertEqual(reader.resolve_attachment("internal-pdf://paper.txt", self.pdf_root)["reason"], "not_a_pdf")

    def test_duplicate_record_ids_are_not_silently_merged(self):
        self.write_xml(self.record_xml() + self.record_xml())
        with self.assertRaisesRegex(reader.ReaderError, "Duplicate"):
            reader.load_records(self.xml, self.pdf_root)

    def test_dtd_and_entities_rejected_in_utf8_and_utf16(self):
        malicious = '<!DOCTYPE xml [<!ENTITY x "expanded">]><xml><records/></xml>'
        for encoding in ("utf-8", "utf-16"):
            self.xml.write_bytes(malicious.encode(encoding))
            with self.assertRaisesRegex(reader.ReaderError, "DTD"):
                reader.load_records(self.xml, self.pdf_root)

    def test_status_and_search_never_open_pdf_contents(self):
        self.write_xml(self.record_xml(links=["internal-pdf://one.pdf"]))
        with patch.object(reader.importlib, "import_module", side_effect=AssertionError("PDF module must not load")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = reader.main(["status", "--xml", str(self.xml), "--pdf-root", str(self.pdf_root)])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["pdf_text_was_read"])

    def test_existing_output_remains_byte_for_byte_unchanged(self):
        self.write_xml(self.record_xml())
        output_path = self.root / "existing.json"
        original = b"previous result\x00keep"
        output_path.write_bytes(original)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = reader.main(["status", "--xml", str(self.xml), "--output", str(output_path)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertEqual(output_path.read_bytes(), original)

    @unittest.skipUnless(importlib.util.find_spec("fitz"), "PyMuPDF unavailable; extraction functional check skipped")
    def test_real_pdf_extraction_ranges_blank_pages_and_multiple_attachments(self):
        import fitz
        path = self.pdf_root / "article.pdf"
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), "Physical page one: claim A")
            document.new_page()
            document.new_page().insert_text((72, 72), "Physical page three: claim B")
            document.save(path)
        self.write_xml(self.record_xml(links=["internal-pdf://article.pdf", "internal-pdf://missing-supplement.pdf"]))
        record = reader.load_records(self.xml, self.pdf_root)[0]
        with self.assertRaisesRegex(reader.ReaderError, "multiple attachments"):
            reader.read_pdf(record, 1, 3)
        result = reader.read_pdf(record, 1, 3, 1)
        self.assertEqual(result["pdf"]["total_pdf_pages"], 3)
        self.assertEqual(result["pdf"]["coverage"], "whole_pdf")
        self.assertIn("claim A", result["pages"][0]["text"])
        self.assertEqual(result["pages"][1]["extraction_status"], "empty_requires_visual_or_ocr")
        self.assertEqual(result["pages"][2]["pdf_page"], 3)
        self.assertEqual(reader.read_pdf(record, 3, 3, 1)["pdf"]["coverage"], "partial_pdf")
        for start, end in ((0, 1), (3, 2), (1, 4), (1, 9)):
            with self.assertRaises(reader.ReaderError):
                reader.read_pdf(record, start, end, 1)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf unavailable; fallback check skipped")
    def test_pypdf_fallback_reads_blank_pdf(self):
        from pypdf import PdfWriter
        path = self.pdf_root / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with path.open("wb") as handle:
            writer.write(handle)
        self.write_xml(self.record_xml(links=["internal-pdf://blank.pdf"]))
        record = reader.load_records(self.xml, self.pdf_root)[0]
        with patch.object(reader.importlib, "import_module", side_effect=ImportError("simulate absent PyMuPDF")):
            result = reader.read_pdf(record, 1, 1)
        self.assertEqual(result["pdf"]["backend"], "pypdf")
        self.assertEqual(result["pages"][0]["extraction_status"], "empty_requires_visual_or_ocr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
