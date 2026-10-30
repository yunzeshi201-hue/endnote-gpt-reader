"""Synthetic tests; no internet requests, real papers or EndNote UI actions."""
import copy
import io
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from xml.sax.saxutils import escape

import reference_pipeline as pipeline


class FakeResponse(io.BytesIO):
    def __init__(self, payload, url="https://public.example/paper.pdf", content_type="application/pdf"):
        super().__init__(payload)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}
        self.url = url

    def geturl(self):
        return self.url


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reference-pipeline-tests-")
        self.root = Path(self.temp.name)
        self.pdf = self.root / "example paper.pdf"
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with self.pdf.open("wb") as handle:
            writer.write(handle)
        self.hash = pipeline.sha256_file(self.pdf)
        self.pdf_root = self.root / "Library.Data" / "PDF"
        self.pdf_root.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def reference(self, identifier="ref-001", doi="10.1234/example", status="verified"):
        return {"id": identifier, "raw_citation": "Doe J (2024). Example paper. Journal. doi:10.1234/example",
                "source_pages": [1], "metadata": {"status": status, "title": "Example paper",
                "doi": doi, "authors": ["Doe, Jane"], "year": "2024", "journal": "Journal",
                "volume": "1", "issue": "2", "pages": "10-14", "type": "journal-article",
                "verified_sources": ["https://publisher.example/article"]},
                "pdf": {"path": str(self.pdf), "status": "downloaded", "source_url": "https://publisher.example/article.pdf", "sha256": self.hash}}

    def manifest(self, references=None):
        references = references if references is not None else [self.reference()]
        return {"schema_version": 2, "source_article": {"path": str(self.pdf), "reference_list_complete": True,
                "expected_reference_count": len(references), "pages": [1]}, "references": references}

    def xml(self, attachments=None, duplicate=False, doi="10.1234/example", title="Example paper", author="Doe, Jane", year="2024"):
        attachments = attachments or []
        rows = []
        for number in (17, 18) if duplicate else (17,):
            urls = "".join("<url>" + escape(link) + "</url>" for link in attachments)
            rows.append(f"""<record><rec-number>{number}</rec-number><titles><title>{escape(title)}</title></titles>
            <contributors><authors><author>{escape(author)}</author></authors></contributors><dates><year>{escape(year)}</year></dates>
            <electronic-resource-num>{escape(doi)}</electronic-resource-num><urls><pdf-urls>{urls}</pdf-urls></urls></record>""")
        path = self.root / ("duplicate.xml" if duplicate else "readback.xml")
        path.write_text("<xml><records>" + "".join(rows) + "</records></xml>", encoding="utf-8")
        return path

    def test_ris_newlines_cannot_inject_tags(self):
        reference = self.reference()
        reference["metadata"]["title"] = "Title\nER  -\nTY  - BOOK\x00"
        reference["metadata"]["authors"] = ["Doe, Jane\r\nL1  - file:///secret.pdf"]
        ledger = pipeline.prepare(self.manifest([reference]), self.root / "prepare")
        ris = (self.root / "prepare" / "references.ris").read_text(encoding="utf-8")
        self.assertEqual(sum(line.startswith("TY  -") for line in ris.splitlines()), 1)
        self.assertEqual(sum(line.startswith("ER  -") for line in ris.splitlines()), 1)
        self.assertFalse(any(line.startswith("L1  -") for line in ris.splitlines()))
        self.assertEqual(ledger["counts"]["new_ris_records"], 1)

    def test_duplicate_doi_creates_one_ris_and_preserves_all_references(self):
        refs = [self.reference(), self.reference("ref-002", "https://doi.org/10.1234/EXAMPLE")]
        ledger = pipeline.prepare(self.manifest(refs), self.root / "prepare")
        self.assertEqual(ledger["counts"]["original_references"], 2)
        self.assertEqual(ledger["counts"]["unique_papers"], 1)
        self.assertEqual(ledger["counts"]["new_ris_records"], 1)
        self.assertEqual(ledger["references"], refs)
        self.assertEqual(ledger["references"][0]["pdf"]["sha256"], self.hash)

    def test_unverified_and_missing_original_citation_are_not_exported(self):
        first = self.reference(status="ambiguous")
        second = self.reference("ref-002", "10.1234/another")
        second["raw_citation"] = ""
        ledger = pipeline.prepare(self.manifest([first, second]), self.root / "prepare")
        self.assertEqual((self.root / "prepare" / "references.ris").read_text(encoding="utf-8"), "")
        self.assertEqual(ledger["counts"]["metadata_verified"], 0)

    def test_conflicting_verified_same_doi_blocks_import(self):
        second = self.reference("ref-002")
        second["metadata"]["title"] = "Different paper"
        ledger = pipeline.prepare(self.manifest([self.reference(), second]), self.root / "prepare")
        self.assertFalse(ledger["papers"][0]["metadata_eligible"])
        self.assertIn("conflicting_verified_metadata", ledger["papers"][0]["issues"])

    def test_pdf_hash_mismatch_does_not_get_attachment_action(self):
        reference = self.reference()
        reference["pdf"]["sha256"] = "0" * 64
        ledger = pipeline.prepare(self.manifest([reference]), self.root / "prepare")
        self.assertFalse(ledger["papers"][0]["pdf"]["validation"]["ok"])
        self.assertEqual(ledger["counts"]["valid_pdfs"], 0)
        self.assertIn("repair_before_attachment", (self.root / "prepare" / "attachment-plan.csv").read_text(encoding="utf-8-sig"))

    def test_fake_pdf_rejected_even_with_correct_hash(self):
        self.pdf.write_text("<html>Login to access paper</html>", encoding="utf-8")
        reference = self.reference()
        reference["pdf"]["sha256"] = pipeline.sha256_file(self.pdf)
        ledger = pipeline.prepare(self.manifest([reference]), self.root / "prepare")
        self.assertEqual(ledger["papers"][0]["pdf"]["validation"]["reason"], "response_is_not_a_pdf")

    def test_existing_exact_record_not_reimported_but_attachment_plan_retained(self):
        ledger = pipeline.prepare(self.manifest(), self.root / "prepare", self.xml(), self.pdf_root)
        self.assertEqual(ledger["counts"]["new_ris_records"], 0)
        self.assertEqual(ledger["papers"][0]["library_match"]["rec_numbers"], ["17"])
        plan = (self.root / "prepare" / "attachment-plan.csv").read_text(encoding="utf-8-sig")
        self.assertIn("attach_via_EndNote_UI_then_export_XML", plan)

    def test_metadata_present_without_attachment_is_not_complete(self):
        ledger = pipeline.prepare(self.manifest(), self.root / "prepare")
        result = pipeline.verify(ledger, self.xml(), self.root / "verify", self.pdf_root)
        self.assertEqual(result["counts"]["library_records"], 1)
        self.assertEqual(result["counts"]["attachments_verified"], 0)
        self.assertFalse(result["data_readback_complete"])
        self.assertFalse(result["all_complete"])
        report = (self.root / "verify" / "repair-report.md").read_text(encoding="utf-8")
        self.assertIn("attachment_not_verified", report)
        self.assertIn("通过 File Attachments 附加", report)

    def test_unverified_report_never_claims_record_exists_or_advises_attachment(self):
        ledger = pipeline.prepare(self.manifest([self.reference(status="unresolved")]), self.root / "prepare")
        result = pipeline.verify(ledger, self.xml(), self.root / "verify", self.pdf_root)
        self.assertEqual(result["papers"][0]["library_match"]["status"], "not_checked_unverified_metadata")
        self.assertEqual(result["papers"][0]["attachment_verification"]["status"], "blocked_unverified_metadata")
        report = (self.root / "verify" / "repair-report.md").read_text(encoding="utf-8")
        self.assertNotIn("记录存在", report)
        self.assertNotIn("attachment_not_verified", report)
        self.assertNotIn("通过 File Attachments 附加", report)
        self.assertIn("attachment_check_blocked_unverified_metadata", report)

    def test_blocked_reports_repair_the_actual_prerequisite_before_attachment(self):
        cases = [
            ("missing", {"doi": "10.1234/different"}, False, "library_record_missing"),
            ("ambiguous", {"duplicate": True}, False, "library_record_ambiguous"),
            ("conflict", {"title": "Different article"}, False, "library_metadata_conflict"),
            ("invalid_pdf", {}, True, "attachment_check_blocked_invalid_expected_pdf"),
        ]
        for name, xml_args, wrong_hash, expected_issue in cases:
            with self.subTest(name=name):
                reference = self.reference()
                if wrong_hash:
                    reference["pdf"]["sha256"] = "0" * 64
                ledger = pipeline.prepare(self.manifest([reference]), self.root / (name + "-prepare"))
                out = self.root / (name + "-verify")
                result = pipeline.verify(ledger, self.xml(**xml_args), out, self.pdf_root)
                self.assertTrue(result["papers"][0]["attachment_verification"]["status"].startswith("blocked_"))
                report = (out / "repair-report.md").read_text(encoding="utf-8")
                self.assertIn(expected_issue, report)
                self.assertNotIn("attachment_not_verified", report)
                self.assertNotIn("通过 File Attachments 附加", report)

    def test_external_link_hash_match_is_distinct_from_relative_library_attachment(self):
        ledger = pipeline.prepare(self.manifest(), self.root / "prepare")
        external = pipeline.verify(ledger, self.xml([self.pdf.as_uri()]), self.root / "verify-external", self.pdf_root)
        attachment = external["papers"][0]["attachment_verification"]
        self.assertEqual(attachment["status"], "verified")
        self.assertFalse(attachment["copied_into_library_verified"])
        self.assertFalse(attachment["endnote_ui_open_verified"])
        copied = self.pdf_root / "copy.pdf"
        shutil.copyfile(self.pdf, copied)
        absolute = pipeline.verify(ledger, self.xml([copied.as_uri()]), self.root / "verify-absolute", self.pdf_root)
        self.assertEqual(absolute["papers"][0]["attachment_verification"]["storage_status"], "library_directory_absolute_link")
        self.assertFalse(absolute["papers"][0]["attachment_verification"]["copied_into_library_verified"])
        absolute_report = (self.root / "verify-absolute" / "repair-report.md").read_text(encoding="utf-8")
        self.assertIn("attachment_library_absolute_link", absolute_report)
        self.assertIn("相对附件链接", absolute_report)
        relative = pipeline.verify(ledger, self.xml(["internal-pdf://copy.pdf"]), self.root / "verify-relative", self.pdf_root)
        self.assertTrue(relative["papers"][0]["attachment_verification"]["copied_into_library_verified"])
        self.assertEqual(relative["counts"]["attachments_within_library"], 1)
        self.assertTrue(relative["data_readback_complete"])
        self.assertFalse(relative["all_complete"])
        self.assertEqual(ledger["stage"], "prepared")

    def test_duplicate_library_matches_block_verification(self):
        ledger = pipeline.prepare(self.manifest(), self.root / "prepare")
        result = pipeline.verify(ledger, self.xml([self.pdf.as_uri()], duplicate=True), self.root / "verify", self.pdf_root)
        self.assertEqual(result["papers"][0]["library_match"]["status"], "ambiguous")
        self.assertEqual(result["counts"]["library_records"], 0)

    def test_same_doi_with_different_title_and_author_is_a_conflict_not_reuse(self):
        xml = self.xml([self.pdf.as_uri()], title="Clearly different research paper", author="Smith, Alex")
        prepared = pipeline.prepare(self.manifest(), self.root / "prepare", xml, self.pdf_root)
        match = prepared["papers"][0]["library_match"]
        self.assertEqual(match["status"], "metadata_conflict")
        self.assertEqual(match["rec_numbers"], ["17"])
        self.assertIn("title", match["conflicts"][0]["fields"])
        self.assertIn("first_author", match["conflicts"][0]["fields"])
        self.assertEqual(prepared["counts"]["new_ris_records"], 0)
        self.assertEqual((self.root / "prepare" / "references.ris").read_text(encoding="utf-8"), "")
        self.assertIn("repair_before_attachment", (self.root / "prepare" / "attachment-plan.csv").read_text(encoding="utf-8-sig"))
        verification = pipeline.verify(prepared, xml, self.root / "verify", self.pdf_root)
        paper = verification["papers"][0]
        self.assertEqual(paper["library_match"]["status"], "metadata_conflict")
        self.assertEqual(paper["attachment_verification"]["status"], "blocked_library_metadata_conflict")
        self.assertEqual(verification["counts"]["library_records"], 0)
        self.assertEqual(verification["counts"]["attachments_verified"], 0)
        self.assertFalse(verification["data_readback_complete"])

    def test_same_doi_online_print_year_difference_requires_review(self):
        xml = self.xml(year="2023")
        prepared = pipeline.prepare(self.manifest(), self.root / "prepare", xml, self.pdf_root)
        match = prepared["papers"][0]["library_match"]
        self.assertEqual(match["status"], "metadata_conflict")
        self.assertEqual(match["conflicts"][0]["fields"], ["year"])
        self.assertEqual(prepared["counts"]["new_ris_records"], 0)

    def test_no_doi_matches_exact_title_year_and_first_author(self):
        reference = self.reference(doi="")
        ledger = pipeline.prepare(self.manifest([reference]), self.root / "prepare")
        result = pipeline.verify(ledger, self.xml([self.pdf.as_uri()], doi=""), self.root / "verify", self.pdf_root)
        self.assertEqual(result["counts"]["library_records"], 1)

    def test_incomplete_source_is_prominent_and_does_not_get_all_complete(self):
        manifest = self.manifest()
        manifest["source_article"]["reference_list_complete"] = False
        manifest["source_article"]["expected_reference_count"] = 12
        ledger = pipeline.prepare(manifest, self.root / "prepare")
        self.assertFalse(ledger["source_audit"]["complete"])
        self.assertIn("不得报告", (self.root / "prepare" / "repair-report.md").read_text(encoding="utf-8"))

    def test_existing_output_directory_is_not_overwritten(self):
        output = self.root / "prepare"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            pipeline.prepare(self.manifest(), output)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_download_checks_real_pdf_and_redacts_signed_query(self):
        url = "https://public.example/paper.pdf?token=secret-key"
        content = self.pdf.read_bytes()
        with patch.object(pipeline, "validate_public_url"), patch.object(pipeline, "open_public", return_value=FakeResponse(content, url)):
            report = self.root / "download.json"
            result = pipeline.download_pdf(url, self.root / "downloaded.pdf", report)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sha256"], self.hash)
        self.assertNotIn("secret-key", report.read_text(encoding="utf-8"))
        self.assertEqual(pipeline.sha256_file(self.root / "downloaded.pdf"), self.hash)

    def test_html_download_never_becomes_pdf(self):
        with patch.object(pipeline, "validate_public_url"), patch.object(pipeline, "open_public", return_value=FakeResponse(b"<html>login</html>", content_type="text/html")):
            result = pipeline.download_pdf("https://public.example/paper.pdf", self.root / "bad.pdf", self.root / "bad.json")
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "bad.pdf").exists())
        self.assertEqual(result["attempts"], 1)

    def test_download_size_limit_does_not_leave_output(self):
        with patch.object(pipeline, "validate_public_url"), patch.object(pipeline, "MAX_PDF_BYTES", 5), patch.object(pipeline, "open_public", return_value=FakeResponse(self.pdf.read_bytes())):
            result = pipeline.download_pdf("https://public.example/paper.pdf", self.root / "oversize.pdf", self.root / "oversize.json")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "pdf_exceeds_50_MiB")
        self.assertFalse((self.root / "oversize.pdf").exists())

    def test_download_refuses_existing_pdf_without_network(self):
        with patch.object(pipeline, "open_public") as mocked:
            with self.assertRaises(pipeline.PipelineError):
                pipeline.download_pdf("https://public.example/paper.pdf", self.pdf, self.root / "never-created.json")
        self.assertFalse(mocked.called)
        self.assertEqual(pipeline.sha256_file(self.pdf), self.hash)

    def test_network_errors_are_bounded_and_never_echo_credentials(self):
        with patch.object(pipeline, "validate_public_url"), patch.object(pipeline, "open_public", side_effect=URLError("https://public.example/?api_key=secret")) as mocked, patch.object(pipeline.time, "sleep"):
            result = pipeline.download_pdf("https://public.example/paper.pdf", self.root / "absent.pdf", self.root / "failed.json")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("secret", json.dumps(result))

    def test_private_and_credential_urls_are_rejected(self):
        private_address = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(pipeline.socket, "getaddrinfo", return_value=private_address):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.validate_public_url("https://public-looking.example/paper.pdf")
        for url in ("file:///C:/secret.pdf", "http://public.example/paper.pdf", "https://user:password@public.example/paper.pdf"):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.validate_public_url(url)

    def test_redirect_to_private_destination_is_rejected(self):
        handler = pipeline.PublicRedirects()
        with patch.object(pipeline, "validate_public_url", side_effect=pipeline.PipelineError("private")):
            with self.assertRaises(pipeline.PipelineError):
                handler.redirect_request(None, None, 302, "", {}, "https://127.0.0.1/paper.pdf")

    def test_crossref_returns_unconfirmed_candidates_only(self):
        payload = {"message": {"items": [{"DOI": "10.1234/example", "title": ["Example paper"],
                   "author": [{"family": "Doe", "given": "Jane"}], "issued": {"date-parts": [[2024]]}}]}}
        with patch.object(pipeline, "open_public", return_value=FakeResponse(json.dumps(payload).encode(), content_type="application/json")):
            result = pipeline.crossref_lookup("Example paper Doe", None, 5)
        self.assertEqual(result["candidates"][0]["metadata"]["status"], "unresolved")
        self.assertTrue(result["candidates"][0]["candidate_only"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
