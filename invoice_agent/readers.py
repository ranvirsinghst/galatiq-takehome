"""Strict source adapters. They preserve source facts, never make payment decisions."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .errors import AgentError
from .models import ErrorCode


class SourceReadError(AgentError):
    """A source cannot be read safely or unambiguously."""

    def __init__(self, code: str, message: str):
        super().__init__(ErrorCode(code), message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceReadError("SOURCE_PARSE_FAILED", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _csv_map(text: str) -> dict[str, Any] | None:
    rows = list(csv.reader(io.StringIO(text), strict=True))
    if not rows:
        raise SourceReadError("SOURCE_PARSE_FAILED", "Empty CSV source")
    headers = [_header(v) for v in rows[0]]
    data: dict[str, Any] = {"line_items": []}
    if headers == ["field", "value"]:
        current = None
        for number, row in enumerate(rows[1:], 2):
            if not row or not any(row):
                continue
            if len(row) != 2:
                raise SourceReadError(
                    "SOURCE_PARSE_FAILED", f"Expected two CSV fields at row {number}"
                )
            key, value = _header(row[0]), row[1]
            key = "tax_amount" if key == "tax" else key
            if key == "item":
                current = {"item": value, "_location": f"row:{number}"}
                data["line_items"].append(current)
            elif key in {"quantity", "unit_price", "amount"}:
                if current is None or key in current:
                    raise SourceReadError(
                        "SOURCE_PARSE_FAILED", f"Ambiguous item group at row {number}"
                    )
                current[key] = value
            else:
                if key in data and data[key] != value:
                    raise SourceReadError("SOURCE_PARSE_FAILED", f"Conflicting CSV field {key}")
                data[key] = value
        return data
    if not {"invoice_number", "vendor", "date", "item", "qty", "unit_price"} <= set(headers):
        return None
    if len(set(headers)) != len(headers):
        raise SourceReadError("SOURCE_PARSE_FAILED", "Duplicate CSV headers")
    for number, row in enumerate(rows[1:], 2):
        if not row or not any(row):
            continue
        if len(row) != len(headers):
            raise SourceReadError("SOURCE_PARSE_FAILED", f"Wrong CSV column count at row {number}")
        record = dict(zip(headers, row))
        if record["item"]:
            for key in (
                "invoice_number",
                "vendor",
                "date",
                "due_date",
                "currency",
                "payment_terms",
            ):
                if key in record and record[key]:
                    if key in data and data[key] != record[key]:
                        raise SourceReadError(
                            "SOURCE_PARSE_FAILED", f"Conflicting CSV {key} at row {number}"
                        )
                    data[key] = record[key]
            data["line_items"].append(
                {
                    "item": record["item"],
                    "quantity": record["qty"],
                    "unit_price": record["unit_price"],
                    "amount": record.get("line_total"),
                    "_location": f"row:{number}",
                }
            )
        else:
            label = record["unit_price"].strip().lower()
            key = (
                "subtotal"
                if label.startswith("subtotal")
                else "tax_amount"
                if label.startswith("tax")
                else "total"
                if label.startswith("total")
                else ""
            )
            if key:
                value = record.get("line_total", "")
                if key in data and data[key] != value:
                    raise SourceReadError(
                        "SOURCE_PARSE_FAILED", f"Conflicting CSV total at row {number}"
                    )
                data[key] = value
                rate = re.search(r"\(([\d.]+)%\)", label)
                if key == "tax_amount" and rate:
                    data["tax_rate"] = str(Decimal(rate[1]) / 100)
    return data


def _xml_map(text: str) -> dict[str, Any] | None:
    root = ElementTree.fromstring(text)
    if root.tag != "invoice":
        return None
    for tag in ("header", "totals", "payment_terms", "line_items"):
        if len(root.findall(tag)) > 1:
            raise SourceReadError("SOURCE_PARSE_FAILED", f"Repeated XML {tag} container")
    if root.find("header") is None:
        return None
    data: dict[str, Any] = {}
    for group in (root.find("header"), root.find("totals")):
        if group is not None:
            for element in group:
                if element.tag in data:
                    raise SourceReadError(
                        "SOURCE_PARSE_FAILED", f"Duplicate XML field {element.tag}"
                    )
                data[element.tag] = element.text
    data["payment_terms"] = root.findtext("payment_terms")
    data["line_items"] = []
    for index, element in enumerate(root.findall("line_items/item")):
        record = {child.tag: child.text for child in element}
        if len(record) != len(element):
            raise SourceReadError("SOURCE_PARSE_FAILED", "Duplicate XML item fields")
        record["item"] = record.pop("name", None)
        record["_location"] = f"/invoice/line_items/item[{index + 1}]"
        data["line_items"].append(record)
    return data


def read_raw(path: Path) -> tuple[str, Any, list[tuple[str, str]], str]:
    """Return text, optional known-layout mapping, location/snippet pairs and content hash."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SourceReadError(
            "SOURCE_IO_ERROR", f"Cannot read {path.name}: {exc.strerror}"
        ) from exc
    fmt = path.suffix.lower().lstrip(".")
    mapped = None
    locations: list[tuple[str, str]] = []
    try:
        if fmt == "pdf":
            pdf = PdfReader(io.BytesIO(content), strict=True)
            if pdf.is_encrypted and not pdf.decrypt(""):
                raise SourceReadError("SOURCE_PARSE_FAILED", "PDF_ENCRYPTED: password required")
            pages = []
            for page_index, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                pages.append(page_text)
                locations.extend(
                    (f"page:{page_index}/line:{i}", line)
                    for i, line in enumerate(page_text.splitlines(), 1)
                    if line.strip()
                )
            text = "\n".join(pages)
            if not text.strip():
                raise SourceReadError("SOURCE_PARSE_FAILED", "PDF_NO_TEXT: OCR is outside scope")
        else:
            text = content.decode("utf-8-sig")
            locations = [
                (f"line:{i}", line) for i, line in enumerate(text.splitlines(), 1) if line.strip()
            ]
            if fmt == "json":
                value = json.loads(
                    text,
                    parse_float=Decimal,
                    object_pairs_hook=_unique_object,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"Nonfinite JSON number: {value}")
                    ),
                )
                known_fields = {
                    "invoice_number",
                    "vendor",
                    "date",
                    "due_date",
                    "line_items",
                    "subtotal",
                    "tax_rate",
                    "tax_amount",
                    "shipping",
                    "total",
                    "currency",
                    "payment_terms",
                    "revision",
                    "notes",
                }
                known_layout = isinstance(value, dict) and (
                    "invoice_number" in value
                    or "line_items" in value
                    or isinstance(value.get("date"), str)
                    or (bool(value) and set(value) <= known_fields)
                )
                if known_layout:
                    if "line_items" in value and (
                        not isinstance(value["line_items"], list)
                        or any(not isinstance(row, dict) for row in value["line_items"])
                    ):
                        raise SourceReadError(
                            "SOURCE_PARSE_FAILED", "JSON line_items must be an array of objects"
                        )
                    mapped = value
            elif fmt == "xml":
                mapped = _xml_map(text)
            elif fmt == "csv":
                mapped = _csv_map(text)
            elif fmt != "txt":
                raise SourceReadError("SOURCE_PARSE_FAILED", f"Unsupported source format: {fmt}")
    except SourceReadError:
        raise
    except (
        UnicodeDecodeError,
        ValueError,
        csv.Error,
        ElementTree.ParseError,
        DefusedXmlException,
        PdfReadError,
    ) as exc:
        raise SourceReadError(
            "SOURCE_PARSE_FAILED", f"Invalid {fmt} source: {type(exc).__name__}"
        ) from exc
    return text, mapped, locations, hashlib.sha256(content).hexdigest()


def read_source(path: Path, source_id: str):
    """Read one file into the shared immutable source contract."""
    from .models import Evidence, SourceDocument

    text, mapped, locations, digest = read_raw(path)
    return SourceDocument(
        source_id=source_id,
        path=str(path),
        format=path.suffix.lower().lstrip("."),
        content_sha256=digest,
        raw_text=text,
        raw_data=mapped,
        evidence=[
            Evidence(
                evidence_id=f"{source_id}:e{i}",
                source_id=source_id,
                location=location,
                excerpt=excerpt,
            )
            for i, (location, excerpt) in enumerate(locations)
        ],
    )
