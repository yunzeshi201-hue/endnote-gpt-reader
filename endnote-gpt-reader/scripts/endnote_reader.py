#!/usr/bin/env python3
"""Read an EndNote XML export and its explicitly linked PDF attachments.

This helper never calls an AI API or edits an EndNote library. PDF page numbers
are physical, one-based positions, not the page labels printed in the paper.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import ntpath
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
import xml.etree.ElementTree as ET


MAX_XML_BYTES = 64 * 1024 * 1024
MAX_PAGES_PER_READ = 8


class ReaderError(Exception):
    """Expected input or dependency error suitable for the caller."""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node if local_name(child.tag) == name]


def child(node: ET.Element, name: str) -> ET.Element | None:
    return next(iter(children(node, name)), None)


def node_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    # EndNote uses inline <style> elements within titles and other fields.
    return " ".join("".join(node.itertext()).split())


def field(node: ET.Element, name: str) -> str:
    return node_text(child(node, name))


def nested_field(node: ET.Element, parent: str, name: str) -> str:
    container = child(node, parent)
    return field(container, name) if container is not None else ""


def values_below(node: ET.Element, container: str, name: str) -> list[str]:
    parent = child(node, container)
    if parent is None:
        return []
    return [text for item in parent.iter() if local_name(item.tag) == name
            if (text := node_text(item))]


def attachment_urls(node: ET.Element) -> list[str]:
    """Keep linked PDF fields separate from ordinary bibliographic web URLs."""
    urls = child(node, "urls")
    if urls is None:
        return []
    found: list[str] = []
    for group in urls:
        group_name = local_name(group.tag)
        if group_name not in {"pdf-urls", "related-urls"}:
            continue
        for item in group:
            if local_name(item.tag) != "url":
                continue
            # Whitespace within a filename is significant, unlike prose fields.
            value = "".join(item.itertext()).strip()
            # Explicit pdf-urls can contain broken / non-PDF links. Preserve
            # these for diagnostics, rather than pretending they do not exist.
            is_pdf = unquote(value).split("?", 1)[0].split("#", 1)[0].lower().endswith(".pdf")
            if value and (group_name == "pdf-urls" or is_pdf) and value not in found:
                found.append(value)
    return found


def resolve_attachment(raw: str, pdf_root: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"link": raw, "path": None, "available": False}
    try:
        if raw.lower().startswith("internal-pdf://"):
            relative = unquote(raw[len("internal-pdf://"):])
            path = _resolve_relative(relative, pdf_root)
        elif re.match(r"^[A-Za-z]:[\\/]", raw) or raw.startswith("\\\\"):
            path = Path(unquote(raw)).expanduser().resolve()
        else:
            parsed = urlsplit(raw)
            if parsed.scheme.lower() == "file":
                if parsed.query or parsed.fragment:
                    raise ReaderError("file_uri_has_query_or_fragment")
                local = url2pathname(parsed.path)
                if parsed.netloc and parsed.netloc.lower() != "localhost":
                    local = "\\\\" + parsed.netloc + local.replace("/", "\\")
                path = Path(local).expanduser().resolve()
            elif parsed.scheme:
                raise ReaderError("remote_or_unsupported_scheme")
            else:
                decoded = unquote(raw)
                path = (Path(decoded).expanduser().resolve() if Path(decoded).is_absolute()
                        else _resolve_relative(decoded, pdf_root))
        if path.suffix.lower() != ".pdf":
            raise ReaderError("not_a_pdf")
        result["path"] = str(path)
        result["available"] = path.is_file()
        if not result["available"]:
            result["reason"] = "file_not_found"
    except (ReaderError, ValueError, OSError) as exc:
        result["reason"] = str(exc)
    return result


def _resolve_relative(raw: str, pdf_root: Path | None) -> Path:
    if pdf_root is None:
        raise ReaderError("pdf_root_required_for_relative_attachment")
    # Check both POSIX and Windows forms even if this helper is run elsewhere.
    if Path(raw).is_absolute() or ntpath.isabs(raw) or ntpath.splitdrive(raw)[0]:
        raise ReaderError("relative_attachment_has_absolute_path")
    normalized = raw.replace("\\", os.sep).replace("/", os.sep)
    root = pdf_root.expanduser().resolve()
    target = (root / normalized).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ReaderError("attachment_escapes_pdf_root") from exc
    return target


def load_records(xml_path: Path, pdf_root: Path | None) -> list[dict[str, Any]]:
    try:
        if xml_path.stat().st_size > MAX_XML_BYTES:
            raise ReaderError("XML export exceeds the 64 MiB metadata limit")
        data = xml_path.read_bytes()
    except OSError as exc:
        raise ReaderError(f"Cannot read XML export: {exc}") from exc
    # Removing nulls also catches the declarations in UTF-16/UTF-32 exports.
    declarations = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ReaderError("DTD and entity declarations are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ReaderError(f"Invalid XML export: {exc}") from exc
    if not any(local_name(node.tag) == "records" for node in root.iter()):
        raise ReaderError("XML has no EndNote records container; supply an EndNote XML export")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate((item for item in root.iter() if local_name(item.tag) == "record"), 1):
        number = field(node, "rec-number")
        if not number:
            raise ReaderError(f"Record at export position {index} has no rec-number")
        if number in seen:
            raise ReaderError(f"Duplicate rec-number in XML export: {number}")
        seen.add(number)
        authors: list[str] = []
        contributors = child(node, "contributors")
        if contributors is not None:
            authors = values_below(contributors, "authors", "author")
        attachments = [dict(index=i, **resolve_attachment(link, pdf_root))
                       for i, link in enumerate(attachment_urls(node), 1)]
        record = {
            "rec_number": number,
            "title": nested_field(node, "titles", "title"),
            "authors": authors,
            "year": nested_field(node, "dates", "year"),
            "journal": nested_field(node, "titles", "secondary-title"),
            "doi": field(node, "electronic-resource-num"),
            "abstract": field(node, "abstract"),
            "keywords": values_below(node, "keywords", "keyword"),
            "attachments": attachments,
            "fulltext_status": ("available" if any(a["available"] for a in attachments)
                                else "metadata_only"),
        }
        records.append(record)
    return records


def source_info(xml_path: Path, pdf_root: Path | None) -> dict[str, Any]:
    return {
        "xml": str(xml_path.resolve()),
        "xml_modified_utc": datetime.fromtimestamp(xml_path.stat().st_mtime, timezone.utc).isoformat(),
        "pdf_root": str(pdf_root.resolve()) if pdf_root else None,
        "snapshot_notice": "XML is an exported snapshot; changes in EndNote require a fresh export.",
    }


def find_record(records: list[dict[str, Any]], number: str) -> dict[str, Any]:
    for record in records:
        if record["rec_number"] == str(number):
            return record
    raise ReaderError(f"No record with rec-number {number} in this XML export")


def search_records(records: list[dict[str, Any]], query: str, limit: int) -> dict[str, Any]:
    terms = query.casefold().split()
    if not terms:
        raise ReaderError("Search query must contain text")
    if not 1 <= limit <= 100:
        raise ReaderError("--limit must be between 1 and 100")
    ranked = []
    for record in records:
        title = record["title"].casefold()
        doi = record["doi"].casefold()
        combined = " ".join([title, doi, *record["authors"], record["abstract"],
                             *record["keywords"]]).casefold()
        if all(term in combined for term in terms):
            score = sum(4 * (term in title) + 8 * (term in doi) for term in terms)
            ranked.append((score, record))
    ranked.sort(key=lambda pair: -pair[0])
    return {"query": query, "total_matches": len(ranked), "returned": min(limit, len(ranked)),
            "results": [{k: record[k] for k in ("rec_number", "title", "authors", "year", "doi", "fulltext_status")}
                        for _, record in ranked[:limit]]}


def read_pdf(record: dict[str, Any], start: int, end: int,
             attachment_index: int | None = None) -> dict[str, Any]:
    if start < 1 or end < start:
        raise ReaderError("PDF page range must satisfy 1 <= start-page <= end-page")
    if end - start + 1 > MAX_PAGES_PER_READ:
        raise ReaderError(f"Read at most {MAX_PAGES_PER_READ} PDF pages per call; request consecutive batches")
    attachments = record["attachments"]
    if not attachments:
        raise ReaderError("This reference has no linked PDF; only its metadata is available")
    if attachment_index is None:
        if len(attachments) != 1:
            raise ReaderError("This reference has multiple attachments; inspect details and pass --attachment-index")
        selected = attachments[0]
    else:
        if not 1 <= attachment_index <= len(attachments):
            raise ReaderError("--attachment-index is outside the attachment list")
        selected = attachments[attachment_index - 1]
    if not selected["available"]:
        raise ReaderError(f"Selected PDF is unavailable: {selected.get('reason', 'unknown reason')}")
    path = Path(selected["path"])
    backend = "pymupdf"
    try:
        module = importlib.import_module("fitz")
    except ImportError:
        module = None
    pages = []
    try:
        if module is not None:
            with module.open(str(path)) as document:
                if document.needs_pass:
                    raise ReaderError("PDF is password protected")
                total = len(document)
                if end > total:
                    raise ReaderError(f"Requested page {end} exceeds PDF page count {total}")
                for number in range(start, end + 1):
                    text = document[number - 1].get_text("text", sort=True).strip()
                    pages.append({"pdf_page": number, "text": text,
                                  "extraction_status": "text" if text else "empty_requires_visual_or_ocr"})
        else:
            backend = "pypdf"
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ReaderError("PDF reading requires PyMuPDF or pypdf; install one in the active Python environment") from exc
            document = PdfReader(str(path))
            if document.is_encrypted:
                raise ReaderError("PDF is password protected")
            total = len(document.pages)
            if end > total:
                raise ReaderError(f"Requested page {end} exceeds PDF page count {total}")
            for number in range(start, end + 1):
                text = (document.pages[number - 1].extract_text() or "").strip()
                pages.append({"pdf_page": number, "text": text,
                              "extraction_status": "text" if text else "empty_requires_visual_or_ocr"})
    except ReaderError:
        raise
    except Exception as exc:
        raise ReaderError(f"Cannot extract selected PDF: {type(exc).__name__}: {exc}") from exc
    return {"record": {k: record[k] for k in ("rec_number", "title", "authors", "year", "doi")},
            "pdf": {"path": str(path), "attachment_index": selected["index"], "backend": backend,
                    "total_pdf_pages": total, "start_pdf_page": start, "end_pdf_page": end,
                    "page_numbering": "one-based physical PDF positions; printed page labels are unverified",
                    "coverage": "whole_pdf" if start == 1 and end == total else "partial_pdf",
                    "visual_notice": "Text extraction does not inspect figures, images, or complex table layout."},
            "pages": pages}


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "search", "details", "read-pdf"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--xml", type=Path, required=True, help="EndNote XML export (not .enl)")
        sub.add_argument("--pdf-root", type=Path, help="Usually Library.Data/PDF; resolves internal-pdf and relative links")
        sub.add_argument("--output", type=Path, help="Write JSON to a new file; existing paths are never overwritten")
        if name == "search":
            sub.add_argument("--query", required=True)
            sub.add_argument("--limit", type=int, default=10)
        if name in {"details", "read-pdf"}:
            sub.add_argument("--rec-number", required=True, help="Exact EndNote rec-number from this XML")
        if name == "read-pdf":
            sub.add_argument("--start-page", type=int, default=1)
            sub.add_argument("--end-page", type=int, default=3)
            sub.add_argument("--attachment-index", type=int, help="One-based index from details; required for multiple attachments")
    return parser


def emit_json(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(text)
    else:
        try:
            with output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except FileExistsError as exc:
            raise ReaderError(f"Output already exists; choose a new path: {output}") from exc
        except OSError as exc:
            raise ReaderError(f"Cannot write output: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = make_parser().parse_args(argv)
    try:
        if args.output is not None and args.output.exists():
            raise ReaderError(f"Output already exists; choose a new path: {args.output}")
        records = load_records(args.xml, args.pdf_root)
        result: dict[str, Any] = {"ok": True, "source": source_info(args.xml, args.pdf_root)}
        if args.command == "status":
            result.update({"record_count": len(records),
                           "records_with_available_pdf": sum(r["fulltext_status"] == "available" for r in records),
                           "metadata_only_records": sum(r["fulltext_status"] == "metadata_only" for r in records),
                           "multiple_attachment_records": sum(len(r["attachments"]) > 1 for r in records),
                           "pdf_text_was_read": False})
        elif args.command == "search":
            result.update(search_records(records, args.query, args.limit))
        elif args.command == "details":
            result["record"] = find_record(records, args.rec_number)
        else:
            result.update(read_pdf(find_record(records, args.rec_number), args.start_page,
                                   args.end_page, args.attachment_index))
        emit_json(result, args.output)
        return 0
    except ReaderError as exc:
        # Errors go to stdout as machine-readable JSON and never overwrite a file.
        emit_json({"ok": False, "error": str(exc)}, None)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
