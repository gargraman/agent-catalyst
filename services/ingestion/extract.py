"""Content sniffing + per-type text extraction (feature 024, research D7; spec FR-001/FR-002).

Five formats, two small pure-Python libraries + the stdlib (LangChain loaders / `unstructured` rejected for P1, decision
log 2026-11-14). `sniff` refuses an unsupported extension and any content that does not match its declared type BEFORE
anything is stored; `extract` returns structure-aware `Section`s (heading for md/html/docx, 1-based page for pdf) so
chunks can carry citation metadata. Pure + deterministic: no I/O, no network.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_BY_EXT = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": PDF,
    ".docx": DOCX,
}
_TEXT_TYPES = {"text/plain", "text/markdown", "text/html"}


class ExtractError(Exception):
    """A refusal or failure with a stable, client-safe `code` (never echoes file content)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Section:
    text: str
    heading: str | None = None
    page: int | None = None


# ------------------------------------------------------------------------------------------------- sniff


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise ExtractError("content_mismatch", "text file contains NUL bytes")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractError("content_mismatch", "text file is not valid UTF-8") from None


def sniff(name: str, data: bytes) -> str:
    """Media type from the extension, confirmed against the bytes. Raises ExtractError(unsupported_type|content_mismatch)."""
    media = _BY_EXT.get(_ext(name))
    if media is None:
        raise ExtractError("unsupported_type", "supported: .txt .md .html .pdf .docx")
    if media == PDF:
        if not data.startswith(b"%PDF-"):
            raise ExtractError("content_mismatch", "file is not a PDF")
    elif media == DOCX:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise ExtractError("content_mismatch", "zip is not a Word document")
        except zipfile.BadZipFile:
            raise ExtractError("content_mismatch", "file is not a Word document") from None
    else:
        _decode_text(data)
    return media


# ------------------------------------------------------------------------------------------------- extract


def _nonempty(sections: list[Section]) -> list[Section]:
    out = [s for s in sections if s.text.strip()]
    if not out:
        raise ExtractError("no_extractable_text", "no text could be extracted")
    return out


_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


def _markdown(text: str) -> list[Section]:
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            sections.append(Section("\n".join(buf).strip(), heading))
            heading, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    sections.append(Section("\n".join(buf).strip(), heading))
    return sections


class _HtmlSections(HTMLParser):
    _HEADINGS = {"h1", "h2", "h3"}
    _DROP = {"script", "style", "noscript", "template"}
    _BLOCK = {"p", "div", "li", "br", "tr", "section", "article", "h4", "h5", "h6", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self._heading: str | None = None
        self._buf: list[str] = []
        self._drop_depth = 0
        self._in_heading = False
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self._drop_depth += 1
        elif tag in self._HEADINGS:
            self._flush()
            self._in_heading, self._heading_buf = True, []
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self._DROP and self._drop_depth:
            self._drop_depth -= 1
        elif tag in self._HEADINGS and self._in_heading:
            self._in_heading = False
            self._heading = " ".join("".join(self._heading_buf).split()) or None
        elif tag in self._BLOCK:
            self._buf.append("\n")

    def handle_data(self, data):
        if self._drop_depth:
            return
        (self._heading_buf if self._in_heading else self._buf).append(data)

    def _flush(self):
        text = "\n".join(" ".join(l.split()) for l in "".join(self._buf).splitlines())
        text = re.sub(r"\n{2,}", "\n", text).strip()
        self.sections.append(Section(text, self._heading))
        self._buf = []

    def close(self):
        super().close()
        self._flush()


def _html(text: str) -> list[Section]:
    p = _HtmlSections()
    p.feed(text)
    p.close()
    return p.sections


def _pdf(data: bytes) -> list[Section]:
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        pages = list(reader.pages)
        if not pages:
            raise ExtractError("parse_failed", "PDF has no pages")
        texts = [(page.extract_text() or "") for page in pages]
    except ExtractError:
        raise
    except Exception:  # noqa: BLE001 — any parser failure is a client-safe parse_failed, never a 500
        raise ExtractError("parse_failed", "PDF could not be parsed") from None
    return [Section(t.strip(), None, i) for i, t in enumerate(texts, start=1)]


def _docx(data: bytes) -> list[Section]:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        raise ExtractError("parse_failed", "Word document could not be parsed") from None
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []
    for para in document.paragraphs:
        style = (para.style.name if para.style is not None else "") or ""
        if style.startswith("Heading") or style == "Title":
            sections.append(Section("\n".join(buf).strip(), heading))
            heading, buf = para.text.strip() or None, []
        elif para.text.strip():
            buf.append(para.text.strip())
    sections.append(Section("\n".join(buf).strip(), heading))
    return sections


def extract(media_type: str, data: bytes) -> list[Section]:
    """Structure-aware sections for a sniffed media type. Raises ExtractError(no_extractable_text|parse_failed|…)."""
    if media_type == PDF:
        return _nonempty(_pdf(data))
    if media_type == DOCX:
        return _nonempty(_docx(data))
    if media_type in _TEXT_TYPES:
        text = _decode_text(data)
        if media_type == "text/markdown":
            return _nonempty(_markdown(text))
        if media_type == "text/html":
            return _nonempty(_html(text))
        return _nonempty([Section(text.strip())])
    raise ExtractError("unsupported_type", f"unsupported media type {media_type!r}")
