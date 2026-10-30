#!/usr/bin/env python3
"""Candidate lookup, bounded public PDF download, RIS preparation and EndNote read-back.

No model API calls, EndNote database writes, or automatic bibliographic identity
decisions occur here. The host must verify metadata and PDF identity from sources.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import csv
import hashlib
import importlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import endnote_reader


MAX_PDF_BYTES = 50 * 1024 * 1024
TIMEOUT_SECONDS = 25
USER_AGENT = "EndNote-GPT-Reader/2.0 (public bibliographic lookup; local user agent)"


class PipelineError(Exception):
    pass


def one_line(value) -> str:
    # RIS values must never create their own tags or record separators.
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    return " ".join(value.split())


def normalized(value) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", one_line(value)).casefold()))


def canonical_doi(value) -> str:
    value = one_line(value)
    value = re.sub(r"^doi\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = unquote(value).strip()
    return value.casefold() if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def first_author(metadata) -> str:
    authors = metadata.get("authors") or []
    if not isinstance(authors, list) or not authors:
        return ""
    return normalized(str(authors[0]).split(",", 1)[0])


def identity_key(metadata, fallback_id) -> str:
    doi = canonical_doi(metadata.get("doi"))
    if doi:
        return "doi:" + doi
    title = normalized(metadata.get("title"))
    year = one_line(metadata.get("year"))
    author = first_author(metadata)
    return "text:" + "|".join((title, year, author)) if title and year and author else "unresolved:" + fallback_id


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"Cannot read JSON input ({type(exc).__name__})") from exc


def write_json_new(path: Path, payload):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pdf(path: Path) -> dict:
    if not path.is_file():
        raise PipelineError("pdf_file_missing")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise PipelineError("pdf_exceeds_50_MiB")
    with path.open("rb") as handle:
        if b"%PDF-" not in handle.read(1024):
            raise PipelineError("response_is_not_a_pdf")
    try:
        try:
            fitz = importlib.import_module("fitz")
        except ImportError:
            fitz = None
        if fitz is not None:
            with fitz.open(str(path)) as document:
                if document.needs_pass:
                    raise PipelineError("pdf_is_password_protected")
                pages = len(document)
        else:
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise PipelineError("Install PyMuPDF or pypdf to validate PDF structure") from exc
            document = PdfReader(str(path))
            if document.is_encrypted:
                raise PipelineError("pdf_is_password_protected")
            pages = len(document.pages)
        if pages < 1:
            raise PipelineError("pdf_has_no_pages")
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("pdf_parser_rejected_file:" + type(exc).__name__) from exc
    return {"ok": True, "pages": pages, "bytes": path.stat().st_size,
            "sha256": sha256_file(path), "identity_verified_by_script": False}


def report_url(url: str) -> str:
    # Signed PDF links may contain tokens; query strings are never written to logs.
    try:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        port = parts.port
        host = "[" + hostname + "]" if ":" in hostname else hostname
        if port:
            host += ":" + str(port)
        return urlunsplit((parts.scheme, host, parts.path, "query-redacted" if parts.query else "", ""))
    except ValueError:
        return "invalid-url"


def validate_public_url(url: str):
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise PipelineError("Only public HTTPS download URLs are accepted")
        if parts.username is not None or parts.password is not None:
            raise PipelineError("Credentials in download URLs are not accepted")
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        if not addresses:
            raise PipelineError("Download hostname has no addresses")
        for address in addresses:
            if not ipaddress.ip_address(address[4][0]).is_global:
                raise PipelineError("Private, loopback, reserved and link-local destinations are rejected")
    except PipelineError:
        raise
    except (ValueError, OSError) as exc:
        raise PipelineError("Cannot validate public download destination") from exc


class PublicRedirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(url: str, accept: str):
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return build_opener(PublicRedirects()).open(request, timeout=TIMEOUT_SECONDS)


def crossref_lookup(query: str | None, doi: str | None, limit: int):
    if not 1 <= limit <= 20:
        raise PipelineError("--limit must be between 1 and 20")
    if doi:
        clean_doi = canonical_doi(doi)
        if not clean_doi:
            raise PipelineError("Invalid DOI")
        url = "https://api.crossref.org/works/" + quote(clean_doi, safe="")
    else:
        url = "https://api.crossref.org/works?" + urlencode({"query.bibliographic": query, "rows": limit})
    try:
        with open_public(url, "application/json") as response:
            payload = response.read(5 * 1024 * 1024 + 1)
        if len(payload) > 5 * 1024 * 1024:
            raise PipelineError("Crossref response is too large")
        message = json.loads(payload.decode("utf-8"))["message"]
        items = [message] if doi else message.get("items", [])
    except PipelineError:
        raise
    except HTTPError as exc:
        raise PipelineError("Crossref HTTP status " + str(exc.code)) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PipelineError("Crossref lookup failed: " + type(exc).__name__) from exc
    candidates = []
    for item in items[:limit]:
        authors = [one_line(author.get("family", "") + ", " + author.get("given", "")).strip(", ")
                   for author in item.get("author", [])]
        dates = item.get("published", item.get("issued", {})).get("date-parts", [[]])
        year = str(dates[0][0]) if dates and dates[0] else ""
        candidate_doi = canonical_doi(item.get("DOI"))
        source = "https://api.crossref.org/works/" + quote(candidate_doi, safe="") if candidate_doi else report_url(url)
        candidates.append({"metadata": {
            "status": "unresolved", "title": one_line((item.get("title") or [""])[0]),
            "doi": candidate_doi, "authors": authors, "year": year,
            "journal": one_line((item.get("container-title") or [""])[0]),
            "volume": one_line(item.get("volume")), "issue": one_line(item.get("issue")),
            "pages": one_line(item.get("page")), "type": item.get("type", ""),
            "verified_sources": [source]}, "crossref_score": item.get("score"),
            "candidate_only": True})
    return {"schema_version": 2, "lookup_url": report_url(url), "candidates": candidates,
            "notice": "Candidates are not confirmed matches. Compare the original citation, title, authors, year and DOI before setting metadata.status=verified."}


def download_pdf(url: str, output: Path, report: Path):
    if output.exists() or report.exists() or output.resolve() == report.resolve():
        raise PipelineError("PDF and report must be distinct, previously nonexistent paths")
    if not output.parent.is_dir() or not report.parent.is_dir():
        raise PipelineError("Create the PDF and report parent directories first")
    validate_public_url(url)
    attempts = 0
    error = ""
    result = None
    for attempt in range(1, 3):
        attempts = attempt
        temporary = None
        try:
            with open_public(url, "application/pdf,application/octet-stream;q=0.8") as response:
                final_url = response.geturl()
                validate_public_url(final_url)
                content_type = response.headers.get("Content-Type", "").casefold()
                if "text/html" in content_type or "application/xhtml" in content_type:
                    raise PipelineError("server_returned_html_not_pdf")
                declared_size = response.headers.get("Content-Length")
                if declared_size and int(declared_size) > MAX_PDF_BYTES:
                    raise PipelineError("pdf_exceeds_50_MiB")
                with tempfile.NamedTemporaryFile(prefix=".endnote-download-", suffix=".pdf", dir=output.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    received = 0
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        received += len(block)
                        if received > MAX_PDF_BYTES:
                            raise PipelineError("pdf_exceeds_50_MiB")
                        handle.write(block)
            result = validate_pdf(temporary)
            # Windows rename is atomic and refuses an existing destination. On
            # POSIX use an atomic exclusive hardlink instead of replacing a file.
            if os.name == "nt":
                os.rename(temporary, output)
            else:
                os.link(temporary, output)
            result.update({"status": "downloaded", "path": str(output.resolve()),
                           "source_url": report_url(url), "resolved_url": report_url(final_url),
                           "attempts": attempts, "identity_notice": "Host must verify this PDF belongs to the intended paper."})
            break
        except PipelineError as exc:
            error = str(exc)
            break
        except HTTPError as exc:
            error = "download_http_status_" + str(exc.code)
            if exc.code not in (408, 429) and exc.code < 500:
                break
        except (OSError, ValueError, URLError) as exc:
            error = "download_failed:" + type(exc).__name__
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if attempt < 2:
            time.sleep(0.25)
    payload = result or {"ok": False, "status": "failed", "error": error,
                         "source_url": report_url(url), "attempts": attempts, "path": None}
    write_json_new(report, payload)
    return payload


def manifest_references(manifest):
    if manifest.get("schema_version") != 2 or not isinstance(manifest.get("references"), list):
        raise PipelineError("Expected schema_version 2 with references array")
    ids = set()
    for reference in manifest["references"]:
        identifier = reference.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise PipelineError("Reference IDs must be nonempty and unique")
        ids.add(identifier)
        if not isinstance(reference.get("metadata"), dict) or not isinstance(reference.get("pdf"), dict):
            raise PipelineError("Every reference needs metadata and pdf objects")
    return manifest["references"]


def source_audit(manifest):
    source = manifest.get("source_article") or {}
    reasons = []
    path = source.get("path")
    if not path or not Path(path).is_absolute() or not Path(path).is_file():
        reasons.append("source_article_file_not_available")
    if source.get("reference_list_complete") is not True:
        reasons.append("reference_list_not_confirmed_complete")
    count = source.get("expected_reference_count")
    if count is not None and (not isinstance(count, int) or count != len(manifest["references"])):
        reasons.append("expected_reference_count_does_not_match_extracted_references")
    if not source.get("pages"):
        reasons.append("reference_list_source_pages_missing")
    return {"complete": not reasons, "reasons": reasons, "expected_reference_count": count}


def metadata_problems(reference):
    metadata = reference["metadata"]
    issues = []
    if metadata.get("status") != "verified":
        issues.append("metadata_not_verified")
    if not one_line(reference.get("raw_citation")):
        issues.append("original_citation_missing")
    if not one_line(metadata.get("title")):
        issues.append("verified_title_missing")
    if not first_author(metadata):
        issues.append("verified_authors_missing")
    if not re.fullmatch(r"\d{4}", one_line(metadata.get("year"))):
        issues.append("verified_year_missing_or_invalid")
    if metadata.get("doi") and not canonical_doi(metadata.get("doi")):
        issues.append("invalid_doi")
    sources = metadata.get("verified_sources")
    if not isinstance(sources, list) or not any(str(value).startswith("https://") for value in sources):
        issues.append("verification_source_missing")
    return issues


def checked_pdf(pdf):
    result = dict(pdf)
    result["validation"] = {"ok": False}
    try:
        if pdf.get("status") != "downloaded":
            raise PipelineError("pdf_not_downloaded")
        path = Path(pdf.get("path") or "")
        if not path.is_absolute():
            raise PipelineError("pdf_path_must_be_absolute")
        expected = str(pdf.get("sha256") or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise PipelineError("expected_pdf_sha256_missing_or_invalid")
        validation = validate_pdf(path)
        if validation["sha256"] != expected:
            raise PipelineError("pdf_sha256_mismatch")
        result["path"] = str(path.resolve())
        result["sha256"] = expected
        result["validation"] = validation
    except (PipelineError, OSError) as exc:
        result["validation"] = {"ok": False, "reason": str(exc)}
    return result


def match_records(metadata, records):
    """Classify library candidates; a DOI match alone never authorizes reuse."""
    doi = canonical_doi(metadata.get("doi"))
    if doi:
        candidates = [record for record in records if canonical_doi(record.get("doi")) == doi]
        expected = {"title": normalized(metadata.get("title")), "first_author": first_author(metadata),
                    "year": one_line(metadata.get("year"))}
        conflicts = []
        for record in candidates:
            observed = {"title": normalized(record.get("title")), "first_author": first_author(record),
                        "year": one_line(record.get("year"))}
            fields = [name for name in expected if not expected[name] or not observed[name] or expected[name] != observed[name]]
            if fields:
                conflicts.append({"rec_number": record["rec_number"], "fields": fields,
                                  "expected": expected, "observed": observed})
        if conflicts:
            # Preserve every same-DOI candidate and block new RIS as well as
            # attachment actions. A print/online year difference is not guessed.
            return {"status": "metadata_conflict", "rec_numbers": [record["rec_number"] for record in candidates],
                    "conflicts": conflicts}
    else:
        title = normalized(metadata.get("title"))
        year = one_line(metadata.get("year"))
        author = first_author(metadata)
        candidates = ([record for record in records if normalized(record.get("title")) == title
                       and one_line(record.get("year")) == year and first_author(record) == author]
                      if title and year and author else [])
    return {"status": "unique" if len(candidates) == 1 else "ambiguous" if candidates else "missing",
            "rec_numbers": [record["rec_number"] for record in candidates], "conflicts": []}


def ris_record(paper):
    metadata = paper["metadata"]
    ris_type = {"journal-article": "JOUR", "book": "BOOK", "book-chapter": "CHAP",
                "proceedings-article": "CPAPER", "dissertation": "THES", "dataset": "DATA"}.get(metadata.get("type"), "GEN")
    rows = [("TY", ris_type), ("TI", metadata.get("title")), ("PY", metadata.get("year"))]
    rows += [("AU", author) for author in metadata.get("authors", [])]
    rows += [("JO", metadata.get("journal")), ("VL", metadata.get("volume")), ("IS", metadata.get("issue"))]
    pages = one_line(metadata.get("pages"))
    page_parts = re.split(r"\s*[-–]\s*", pages, maxsplit=1)
    if pages:
        rows.append(("SP", page_parts[0]))
        if len(page_parts) == 2:
            rows.append(("EP", page_parts[1]))
    rows += [("DO", canonical_doi(metadata.get("doi"))),
             ("N1", "Pipeline reference IDs: " + ", ".join(paper["reference_ids"]))]
    return "\n".join(f"{tag}  - {one_line(value)}" for tag, value in rows if one_line(value)) + "\nER  -\n\n"


def counts_for(ledger, verification=False):
    papers = ledger["papers"]
    return {
        "original_references": len(ledger["references"]), "unique_papers": len(papers),
        "metadata_verified": sum(paper["metadata_eligible"] for paper in papers),
        "valid_pdfs": sum(paper["pdf"]["validation"]["ok"] for paper in papers),
        "library_records": sum(paper.get("library_match", {}).get("status") == "unique" for paper in papers),
        "attachments_verified": sum(paper.get("attachment_verification", {}).get("status") == "verified" for paper in papers),
        "attachments_within_library": sum(paper.get("attachment_verification", {}).get("copied_into_library_verified") is True for paper in papers),
        "new_ris_records": sum(paper.get("import_state") == "ris_prepared_not_imported" for paper in papers),
        "metadata_unresolved_references": sum(bool(metadata_problems(reference)) for reference in ledger["references"]),
        "library_readback_performed": verification,
    }


REPAIRS = {
    "metadata_not_verified": "Compare original citation with DOI resolver, publisher, Crossref or repository metadata; keep candidates unresolved until matched.",
    "original_citation_missing": "Extract and retain the original numbered citation and its source page before importing.",
    "verified_title_missing": "Verify and add the full publication title.",
    "verified_authors_missing": "Verify authors; store them in Family, Given order.",
    "verified_year_missing_or_invalid": "Verify the publication year and store four digits.",
    "invalid_doi": "Correct DOI from an authoritative record; do not invent it.",
    "verification_source_missing": "Record the official metadata source URL used by the host.",
    "conflicting_verified_metadata": "Resolve conflicting titles or years for the same DOI before importing.",
    "multiple_pdf_versions": "Inspect both PDFs and explicitly select the intended paper/version in the manifest.",
    "pdf_not_downloaded": "Locate a lawful public/full-text source or obtain the PDF through the user's institutional access; retain failure if unavailable.",
    "pdf_sha256_mismatch": "Do not attach the changed file; inspect/download the intended PDF and update its checksum only after identity review.",
    "library_record_missing": "Import the prepared RIS into the intended EndNote library, then export that library to XML again.",
    "library_record_ambiguous": "Inspect duplicate EndNote records and select/merge through EndNote; export XML again.",
    "attachment_not_verified": "Attach the planned PDF to the matched record through EndNote File Attachments; export XML with attachment links and verify again.",
}

ZH_REPAIRS = {
    "metadata_not_verified": "元数据仍是候选。对照原始引用、出版社或 DOI 记录逐项核对题名、作者、年份和 DOI；核对后才将 metadata.status 设为 verified。",
    "original_citation_missing": "缺少原始引用。回到原文章的参考文献页，补上原始编号、完整引用和 source_pages。",
    "verified_title_missing": "缺少已核实题名。用出版社或 DOI 记录补齐完整题名。",
    "verified_authors_missing": "缺少已核实作者。核对作者名单，并按 Family, Given 保存。",
    "verified_year_missing_or_invalid": "年份缺失或无效。核对出版年份，保存为四位数字。",
    "invalid_doi": "DOI 格式无效。核对出版社的准确 DOI；查不到时保留空值，不猜测。",
    "verification_source_missing": "缺少核对来源。在 verified_sources 中记录实际用于核对的官方 HTTPS 页面。",
    "conflicting_verified_metadata": "同一 DOI 对应的已核实题名或年份冲突。逐条复核原始引用，修正冲突后重新准备。",
    "multiple_pdf_versions": "同一论文有多个不同哈希的 PDF。人工检查版本和论文身份，在 manifest 中明确保留要附加的版本。",
    "pdf_not_downloaded": "尚无成功下载的 PDF。查找出版社开放版本、机构仓储或用户已获授权的全文；不可访问时保留失败原因。",
    "pdf_sha256_mismatch": "本地文件与记录的下载哈希不符。重新核对文件身份；只有核对正确版本后才更新路径和哈希。",
    "expected_pdf_sha256_missing_or_invalid": "缺少有效 SHA-256。用下载报告中的 sha256 填入 manifest，并重新验证本地 PDF。",
    "pdf_file_missing": "计划 PDF 不存在。恢复该文件或重新取得正确 PDF，更新绝对路径后重跑。",
    "pdf_path_must_be_absolute": "PDF 路径必须为绝对路径。补上实际文件的完整路径。",
    "response_is_not_a_pdf": "文件不是可识别的 PDF。可能下载到了登录页或错误页，换用可访问的 PDF 地址。",
    "pdf_is_password_protected": "PDF 需要密码。用用户有权访问的可读取版本重试。",
    "library_record_missing": "回读 XML 中没有精确匹配的记录。在目标 EndNote 库导入已生成的 RIS，再导出含附件链接的新 XML。",
    "library_record_ambiguous": "回读 XML 中有多个匹配记录。用 EndNote 查看重复记录，人工选择或合并后重新导出，勿再次创建重复项。",
    "library_metadata_conflict": "DOI 相同但题名、第一作者或年份不一致（包括 online/print 年份差异）。查看 library_match.conflicts 和候选 rec_numbers，对照出版记录人工核对；修正后重新导出 XML。核对前不要新增重复记录或附加 PDF。",
    "attachment_not_verified": "记录存在但未验证到计划 PDF。打开该 EndNote 记录，通过 File Attachments 附加正确文件；如需复制入库，使用库内相对附件，再导出新 XML 核验。",
    "attachment_external_link": "附件哈希匹配，但路径仍在指定库附件目录之外。它是外部链接，不能称为已复制入库；如需库内副本，用 EndNote 复制为相对附件并重新导出。",
    "attachment_library_absolute_link": "PDF 位于指定库附件目录内，但 XML 中仍是绝对链接。用 EndNote 将附件保存/转换为该库的相对附件链接，再导出新 XML 核验；不能仅凭目录位置称相对附件已完成。",
    "attachment_check_blocked_unverified_metadata": "论文身份尚未核验，本次尚未匹配或核验目标 EndNote 记录。先核对原始引用与元数据，再准备和回读；此时不要给未知记录附加文件。",
    "attachment_check_blocked_invalid_expected_pdf": "计划 PDF 缺失、无效或与下载哈希不符，本次没有完成附件比对。先补齐或修复计划 PDF，核对论文身份、路径及 SHA-256，再重新准备和回读；不要直接附加当前文件。",
    "attachment_check_blocked_other": "附件检查被前置问题阻止。先处理本条列出的身份、记录或计划 PDF 问题，再重新回读；本状态不证明记录存在或附件缺失。",
    "source_article_file_not_available": "原文章文件不可用。提供实际原文绝对路径；未提供前不能声称参考文献提取已完整。",
    "reference_list_not_confirmed_complete": "尚未确认参考文献列表完整。检查参考文献所有页及续页，完成后才标记 reference_list_complete=true。",
    "expected_reference_count_does_not_match_extracted_references": "预期引用数量与提取数量不符。逐一核对编号，补齐漏项或修正预期数量。",
    "reference_list_source_pages_missing": "缺少参考文献来源页码。记录实际参考文献所在的 PDF 物理页。",
}


def repair_report(ledger):
    lines = ["# 参考文献处理与修复报告", "",
             "本报告按实际证据记录状态。生成 RIS 不代表已导入 EndNote；附件哈希匹配也不代表已复制到库内。脚本不替代在 EndNote 界面逐条打开附件的核验。", ""]
    if not ledger["source_audit"]["complete"]:
        lines += ["**参考文献提取尚未完整：不得报告“全部完成”。**", ""]
        for reason in ledger["source_audit"]["reasons"]:
            lines.append("- 状态：" + reason + "。修复：" + ZH_REPAIRS.get(reason, "核对原文后更新 manifest。"))
        lines.append("")
    lines += ["计数：`" + json.dumps(ledger["counts"], ensure_ascii=False) + "`", "",
              "修复后使用新的输出目录重跑。命令中的 XML、PDF 目录和 manifest 路径应替换为实际绝对路径：", "",
              "```powershell", 'python reference_pipeline.py prepare --manifest "修正后的manifest.json" --out-dir "新的准备目录" --existing-xml "目标库最新.xml" --pdf-root "目标库.Data\\PDF"',
              'python reference_pipeline.py verify --manifest "准备目录\\ledger.json" --endnote-xml "目标库重新导出.xml" --pdf-root "目标库.Data\\PDF" --out-dir "新的核验目录"', "```", ""]
    for paper in ledger["papers"]:
        lines += ["## " + one_line(paper["paper_id"]) + " — " + one_line(paper["metadata"].get("title")),
                  "", "原始引用 ID：" + ", ".join(paper["reference_ids"]), ""]
        issues = list(paper.get("issues", []))
        pdf_reason = paper["pdf"]["validation"].get("reason")
        if pdf_reason:
            issues.append(pdf_reason)
        match_status = paper.get("library_match", {}).get("status")
        if match_status == "missing":
            issues.append("library_record_missing")
        elif match_status == "ambiguous":
            issues.append("library_record_ambiguous")
        elif match_status == "metadata_conflict":
            issues.append("library_metadata_conflict")
        attachment_status = paper.get("attachment_verification", {}).get("status")
        expected_pdf_valid = paper["pdf"]["validation"].get("ok") is True
        if (attachment_status == "missing_or_hash_mismatch"
                and match_status == "unique" and expected_pdf_valid):
            issues.append("attachment_not_verified")
        elif attachment_status == "blocked_unverified_metadata":
            issues.append("attachment_check_blocked_unverified_metadata")
        elif attachment_status == "blocked_invalid_expected_pdf":
            issues.append("attachment_check_blocked_invalid_expected_pdf")
        elif attachment_status in {"blocked_library_missing", "blocked_library_ambiguous", "blocked_library_metadata_conflict"}:
            # The record-stage issue above is the repair frontier. A blocked
            # attachment check is not evidence that a matched record exists.
            pass
        elif attachment_status and attachment_status != "verified":
            issues.append("attachment_check_blocked_other")
        elif attachment_status == "verified" and paper.get("attachment_verification", {}).get("storage_status") == "external_link_or_storage_unverified":
            issues.append("attachment_external_link")
        elif attachment_status == "verified" and paper.get("attachment_verification", {}).get("storage_status") == "library_directory_absolute_link":
            issues.append("attachment_library_absolute_link")
        if not issues:
            lines.append("- 状态：本阶段未发现额外问题。仍需按下方实际状态区分准备、入库回读、附件哈希和界面打开验证。")
        for issue in dict.fromkeys(issues):
            lines.append("- 状态/原因：" + one_line(issue) + "。修复动作：" + ZH_REPAIRS.get(issue, "检查上述具体原因，修正对应输入或附件；随后使用本报告开头的命令重跑到新目录。"))
        lines += ["- 导入状态：" + paper.get("import_state", "not_checked"),
                  "- 附件哈希核验：" + (attachment_status or "not_performed"),
                  "- 附件存储状态：" + paper.get("attachment_verification", {}).get("storage_status", "not_checked"), ""]
    lines += ["全部完成：" + str(ledger.get("all_complete", False)),
              "界面逐条打开证据由执行此 skill 的 host 另行记录，脚本不会推断已打开。", ""]
    return "\n".join(lines)


def prepare(manifest, out_dir: Path, existing_xml: Path | None = None, pdf_root: Path | None = None):
    references = manifest_references(manifest)
    existing = endnote_reader.load_records(existing_xml, pdf_root) if existing_xml else []
    groups = {}
    for reference in references:
        key = identity_key(reference["metadata"], reference["id"])
        groups.setdefault(key, []).append(reference)
    papers = []
    for index, (key, members) in enumerate(groups.items(), 1):
        eligible = [member for member in members if not metadata_problems(member)]
        primary = eligible[0] if eligible else members[0]
        metadata = dict(primary["metadata"])
        metadata["doi"] = canonical_doi(metadata.get("doi"))
        issues = sorted({issue for member in members for issue in metadata_problems(member)})
        conflict = len({(normalized(member["metadata"].get("title")), one_line(member["metadata"].get("year"))) for member in eligible}) > 1
        metadata_eligible = bool(eligible) and not conflict
        if conflict:
            issues.append("conflicting_verified_metadata")
        pdfs = [checked_pdf(member["pdf"]) for member in eligible or [primary]]
        valid = [pdf for pdf in pdfs if pdf["validation"]["ok"]]
        pdf = valid[0] if valid else pdfs[0]
        if len({item["sha256"] for item in valid}) > 1:
            issues.append("multiple_pdf_versions")
            pdf = dict(pdf, validation={"ok": False, "reason": "multiple_pdf_versions"})
        library_match = (match_records(metadata, existing) if metadata_eligible and existing_xml
                         else {"status": "not_checked", "rec_numbers": [], "conflicts": []})
        import_state = ("metadata_unresolved_not_exported" if not metadata_eligible
                        else "library_metadata_conflict_not_exported" if library_match["status"] == "metadata_conflict"
                        else "existing_record_not_reimported" if library_match["rec_numbers"]
                        else "ris_prepared_not_imported")
        papers.append({"paper_id": f"paper-{index:04d}", "identity_key": key,
                       "reference_ids": [member["id"] for member in members], "metadata": metadata,
                       "metadata_eligible": metadata_eligible, "pdf": pdf, "issues": issues,
                       "library_match": library_match,
                       "import_state": import_state})
    ledger = {"schema_version": 2, "stage": "prepared", "created_utc": datetime.now(timezone.utc).isoformat(),
              "source_article": manifest.get("source_article", {}), "source_audit": source_audit(manifest),
              "references": references, "papers": papers, "all_complete": False,
              "existing_xml": str(existing_xml.resolve()) if existing_xml else None}
    ledger["counts"] = counts_for(ledger)
    out_dir.mkdir(parents=False, exist_ok=False)
    with (out_dir / "references.ris").open("x", encoding="utf-8", newline="\n") as handle:
        for paper in papers:
            if paper["import_state"] == "ris_prepared_not_imported":
                handle.write(ris_record(paper))
    with (out_dir / "attachment-plan.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["paper_id", "reference_ids", "doi", "title", "existing_rec_number", "pdf_path", "sha256", "action"])
        writer.writeheader()
        for paper in papers:
            action = ("attach_via_EndNote_UI_then_export_XML" if paper["metadata_eligible"] and paper["pdf"]["validation"]["ok"] and paper["library_match"]["status"] in {"unique", "missing", "not_checked"}
                      else "repair_before_attachment")
            writer.writerow({"paper_id": paper["paper_id"], "reference_ids": ";".join(paper["reference_ids"]),
                             "doi": paper["metadata"].get("doi", ""), "title": one_line(paper["metadata"].get("title")),
                             "existing_rec_number": ";".join(paper["library_match"]["rec_numbers"]),
                             "pdf_path": paper["pdf"].get("path") or "", "sha256": paper["pdf"].get("sha256") or "", "action": action})
    write_json_new(out_dir / "ledger.json", ledger)
    (out_dir / "repair-report.md").write_text(repair_report(ledger), encoding="utf-8")
    return ledger


def verify(ledger, endnote_xml: Path, out_dir: Path, pdf_root: Path | None = None):
    manifest_references(ledger)
    if not isinstance(ledger.get("papers"), list):
        raise PipelineError("verify requires a ledger.json produced by prepare")
    records = endnote_reader.load_records(endnote_xml, pdf_root)
    # Deep copy keeps the prepared ledger untouched.
    result = json.loads(json.dumps(ledger))
    result.update({"stage": "verified", "verification_xml": str(endnote_xml.resolve()),
                   "verified_utc": datetime.now(timezone.utc).isoformat()})
    for paper in result["papers"]:
        if not paper.get("metadata_eligible"):
            paper["library_match"] = {"status": "not_checked_unverified_metadata", "rec_numbers": []}
            paper["attachment_verification"] = {"status": "blocked_unverified_metadata"}
            continue
        paper["library_match"] = match_records(paper["metadata"], records)
        status = paper["library_match"]["status"]
        if status != "unique":
            if status == "metadata_conflict":
                paper["import_state"] = "library_metadata_conflict_requires_review"
            paper["attachment_verification"] = {"status": "blocked_library_" + status}
            continue
        matched_record = next(record for record in records if record["rec_number"] == paper["library_match"]["rec_numbers"][0])
        paper["import_state"] = "verified_in_exported_library"
        expected = checked_pdf(paper["pdf"])
        paper["pdf"] = expected
        if not expected["validation"]["ok"]:
            paper["attachment_verification"] = {"status": "blocked_invalid_expected_pdf"}
            continue
        observed = []
        for attachment in matched_record["attachments"]:
            entry = dict(attachment)
            entry["within_pdf_root"] = False
            link = attachment["link"]
            entry["link_kind"] = ("internal_pdf_relative" if link.casefold().startswith("internal-pdf://")
                                  else "absolute_file_link" if urlsplit(link).scheme or Path(link).is_absolute()
                                  else "relative_file_link")
            if attachment["available"]:
                try:
                    attached_path = Path(attachment["path"])
                    if attached_path.stat().st_size != expected["validation"]["bytes"]:
                        entry["matches_expected"] = False
                        entry["reason"] = "attachment_size_differs_from_expected_pdf"
                    else:
                        entry["sha256"] = sha256_file(attached_path)
                        entry["matches_expected"] = entry["sha256"] == expected["sha256"]
                    if pdf_root is not None:
                        try:
                            Path(attachment["path"]).resolve().relative_to(pdf_root.resolve())
                            entry["within_pdf_root"] = True
                        except ValueError:
                            pass
                except OSError:
                    entry["matches_expected"] = False
                    entry["read_error"] = "attachment_file_unreadable"
            observed.append(entry)
        matched = [item for item in observed if item.get("matches_expected")]
        relative_in_library = any(item.get("within_pdf_root") and item.get("link_kind") in {"internal_pdf_relative", "relative_file_link"} for item in matched)
        paper["attachment_verification"] = {"status": "verified" if matched else "missing_or_hash_mismatch",
                                              "storage_status": "library_relative_attachment" if relative_in_library else "library_directory_absolute_link" if any(item.get("within_pdf_root") for item in matched) else "external_link_or_storage_unverified" if matched else "not_verified",
                                              "copied_into_library_verified": relative_in_library,
                                              "endnote_ui_open_verified": False,
                                              "expected_sha256": expected["sha256"], "observed": observed}
    result["source_audit"] = source_audit(result)
    result["counts"] = counts_for(result, verification=True)
    result["data_readback_complete"] = bool(result["papers"]) and result["source_audit"]["complete"] and result["counts"]["metadata_unresolved_references"] == 0 and all(
        paper.get("metadata_eligible") and paper.get("library_match", {}).get("status") == "unique"
        and paper.get("attachment_verification", {}).get("status") == "verified" for paper in result["papers"])
    # A host must separately record real UI-open evidence; script evidence alone
    # cannot honestly establish the entire requested workflow as complete.
    result["all_complete"] = False
    out_dir.mkdir(parents=False, exist_ok=False)
    write_json_new(out_dir / "verification.json", result)
    (out_dir / "repair-report.md").write_text(repair_report(result), encoding="utf-8")
    return result


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    lookup = subs.add_parser("crossref", help="Return candidates; do not mark them verified")
    choice = lookup.add_mutually_exclusive_group(required=True)
    choice.add_argument("--query")
    choice.add_argument("--doi")
    lookup.add_argument("--limit", type=int, default=5)
    lookup.add_argument("--output", type=Path, required=True)
    download = subs.add_parser("download", help="Download a public PDF, validate structure and hash")
    download.add_argument("--url", required=True)
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--report", type=Path, required=True)
    prep = subs.add_parser("prepare", help="Prepare verified metadata RIS and explicit attachment plan")
    prep.add_argument("--manifest", type=Path, required=True)
    prep.add_argument("--out-dir", type=Path, required=True)
    prep.add_argument("--existing-xml", type=Path)
    prep.add_argument("--pdf-root", type=Path)
    verification = subs.add_parser("verify", help="Verify imported records and PDF hashes from fresh XML")
    verification.add_argument("--manifest", type=Path, required=True)
    verification.add_argument("--endnote-xml", type=Path, required=True)
    verification.add_argument("--pdf-root", type=Path)
    verification.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = make_parser().parse_args(argv)
    try:
        if args.command == "crossref":
            if args.output.exists():
                raise PipelineError("Output path already exists")
            result = crossref_lookup(args.query, args.doi, args.limit)
            write_json_new(args.output, result)
        elif args.command == "download":
            result = download_pdf(args.url, args.output, args.report)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 2
        elif args.command == "prepare":
            result = prepare(load_json(args.manifest), args.out_dir, args.existing_xml, args.pdf_root)
        else:
            result = verify(load_json(args.manifest), args.endnote_xml, args.out_dir, args.pdf_root)
        print(json.dumps({"ok": True, "output": str(args.output if args.command == "crossref" else args.out_dir),
                          "counts": result.get("counts"), "all_complete": result.get("all_complete", False)}, ensure_ascii=False))
        return 0
    except (PipelineError, endnote_reader.ReaderError, OSError, ValueError) as exc:
        # Never render urllib exceptions: they can contain signed URLs/secrets.
        message = str(exc) if isinstance(exc, (PipelineError, endnote_reader.ReaderError)) else type(exc).__name__
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
