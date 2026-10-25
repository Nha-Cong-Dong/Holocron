"""Cắt chunk tất định từ văn bản đã xác nhận (SPECIFICATION §12.1, ADR-006 mục 3). LLM không cắt chunk.

Thuật toán (cùng đầu vào ⇒ cùng tập chunk, cùng `chunk_id`):

1. **Đoạn.** Mỗi dòng được gộp khoảng trắng. Dòng rỗng, dòng tiêu đề và hết trang là ranh giới đoạn; các dòng liền
   nhau trong một đoạn nối bằng một dấu cách.
2. **Phần theo tiêu đề.** Mỗi dòng tiêu đề mở một phần mới. Phần chỉ có tiêu đề (tiêu đề nằm ngay trên một tiêu đề
   khác) được gộp vào phần sau nó. Dòng tiêu đề nhận theo mẫu không phụ thuộc môn: tiêu đề Markdown (`# …`), từ khoá
   đánh số (`Chương 3`, `Bài 2`, `Phần II`, `Chapter 4`, `Section 1`, …) hoặc số mục nhiều cấp (`3.2 …`, `3.2.1 …`);
   dòng dài quá 120 ký tự hoặc kết thúc bằng dấu câu không phải tiêu đề.
3. **Giới hạn 450 token.** Token là token âm tiết của chỉ mục tìm kiếm (`domain.search_text.search_tokens`). Phần
   dài hơn được tách tại ranh giới đoạn; đoạn không vừa thì tách tại ranh giới câu; câu dài hơn 450 token thì tách
   theo từ. Chunk sau mở đầu bằng câu cuối của chunk trước (chồng lấn 1 câu), trừ khi câu đó cộng với phần tiếp theo
   vượt giới hạn. Chồng lấn không vượt qua ranh giới phần.
4. **Trích dẫn mặc định** (§5.8, khi chưa có LLM): câu đầu tiên đủ 8 ký tự, ưu tiên câu không phải tiêu đề; dài quá
   600 ký tự thì cắt tại ranh giới từ.
5. **`chunk_id`** = `"chk_"` + 16 ký tự hex đầu của sha256 trên `doc_id`, thứ tự chunk và `raw_text`.

Chunk không có token nào hoặc ngắn hơn 8 ký tự bị bỏ (không đủ để trích dẫn).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..domain.schemas import KnowledgeChunk, normalize_quote
from ..domain.search_text import search_tokens
from ..domain.vault import ConfirmedDocumentText

MAX_CHUNK_TOKENS = 450
MAX_HEADING_CHARS = 120
MIN_QUOTE_CHARS = 8
MAX_QUOTE_CHARS = 600

HEADING_RE = re.compile(
    r"^(?:"
    r"#{1,6}\s+\S"
    r"|(?:chương|chapter|bài|phần|part|section|lecture|mục|unit)\s+(?:[0-9]+|[ivxlc]+)\b"
    r"|[0-9]{1,2}(?:\.[0-9]{1,2})+\.?\s+\S"
    r")",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
HEADING_TRAILING_PUNCTUATION = ".,;:!?…"


@dataclass(frozen=True)
class _Paragraph:
    page_number: int
    text: str
    is_heading: bool


@dataclass(frozen=True)
class _Unit:
    """Một câu (hoặc mảnh câu) — đơn vị nhỏ nhất khi tách chunk."""

    paragraph: int
    page_number: int
    text: str
    tokens: int
    is_heading: bool


def is_heading(line: str) -> bool:
    """Dòng (đã gộp khoảng trắng) có phải tiêu đề theo mẫu ở bước 2 không."""
    if not line or len(line) > MAX_HEADING_CHARS or line[-1] in HEADING_TRAILING_PUNCTUATION:
        return False
    return HEADING_RE.match(line) is not None


def _flush(buffer: list[str], page_number: int, paragraphs: list[_Paragraph]) -> None:
    if buffer:
        paragraphs.append(_Paragraph(page_number, " ".join(buffer), is_heading=False))
        buffer.clear()


def _paragraphs(document: ConfirmedDocumentText) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    for page in document.pages:
        buffer: list[str] = []
        for raw_line in page.lines:
            line = normalize_quote(raw_line)
            if not line:
                _flush(buffer, page.page_number, paragraphs)
            elif is_heading(line):
                _flush(buffer, page.page_number, paragraphs)
                paragraphs.append(_Paragraph(page.page_number, line, is_heading=True))
            else:
                buffer.append(line)
        _flush(buffer, page.page_number, paragraphs)
    return paragraphs


def _sections(paragraphs: list[_Paragraph]) -> list[list[_Paragraph]]:
    """Tách theo tiêu đề; phần chỉ có tiêu đề được gộp vào phần sau."""
    sections: list[list[_Paragraph]] = []
    for paragraph in paragraphs:
        if paragraph.is_heading or not sections:
            previous = sections[-1] if sections else None
            if paragraph.is_heading and previous is not None and all(p.is_heading for p in previous):
                previous.append(paragraph)
            else:
                sections.append([paragraph])
        else:
            sections[-1].append(paragraph)
    return sections


def _token_count(text: str) -> int:
    return len(search_tokens(text))


def _split_long_sentence(sentence: str) -> list[str]:
    """Câu dài hơn giới hạn được tách theo từ thành các mảnh ≤ 450 token (một từ quá dài vẫn giữ nguyên)."""
    pieces: list[str] = []
    words: list[str] = []
    count = 0
    for word in sentence.split(" "):
        word_tokens = _token_count(word)
        if words and count + word_tokens > MAX_CHUNK_TOKENS:
            pieces.append(" ".join(words))
            words, count = [], 0
        words.append(word)
        count += word_tokens
    if words:
        pieces.append(" ".join(words))
    return pieces


def _units(index: int, paragraph: _Paragraph) -> list[_Unit]:
    units: list[_Unit] = []
    for sentence in SENTENCE_SPLIT_RE.split(paragraph.text):
        pieces = [sentence] if _token_count(sentence) <= MAX_CHUNK_TOKENS else _split_long_sentence(sentence)
        units.extend(
            _Unit(index, paragraph.page_number, piece, _token_count(piece), paragraph.is_heading) for piece in pieces
        )
    return units


class _Packer:
    """Gom đơn vị thành chunk ≤ 450 token, chồng lấn 1 câu giữa hai chunk liền nhau của cùng một phần."""

    def __init__(self) -> None:
        self.chunks: list[list[_Unit]] = []
        self._current: list[_Unit] = []
        self._has_new = False

    def _total(self) -> int:
        return sum(unit.tokens for unit in self._current)

    def fits(self, tokens: int) -> bool:
        return self._total() + tokens <= MAX_CHUNK_TOKENS

    def add(self, units: list[_Unit]) -> None:
        self._current.extend(units)
        self._has_new = True

    def flush(self) -> None:
        """Đóng chunk hiện tại (nếu có nội dung mới) và giữ câu cuối làm phần chồng lấn của chunk sau."""
        if self._has_new:
            self.chunks.append(list(self._current))
            self._current = self._current[-1:]
            self._has_new = False

    def drop_overlap_if_too_big(self, tokens: int) -> None:
        if not self.fits(tokens) and not self._has_new:
            self._current = []

    def end_section(self) -> None:
        if self._has_new:
            self.chunks.append(list(self._current))
        self._current = []
        self._has_new = False


def _pack(sections: list[list[_Paragraph]]) -> list[list[_Unit]]:
    packer = _Packer()
    index = 0
    for section in sections:
        for paragraph in section:
            units = _units(index, paragraph)
            index += 1
            paragraph_tokens = sum(unit.tokens for unit in units)
            if packer.fits(paragraph_tokens):
                packer.add(units)
                continue
            packer.flush()  # tách tại ranh giới đoạn
            if packer.fits(paragraph_tokens):
                packer.add(units)
                continue
            for unit in units:  # đoạn không vừa: tách tại ranh giới câu
                if not packer.fits(unit.tokens):
                    packer.flush()
                    packer.drop_overlap_if_too_big(unit.tokens)
                packer.add([unit])
        packer.end_section()
    return packer.chunks


def _raw_text(units: list[_Unit]) -> str:
    paragraphs: list[list[str]] = []
    previous: int | None = None
    for unit in units:
        if unit.paragraph != previous:
            paragraphs.append([])
            previous = unit.paragraph
        paragraphs[-1].append(unit.text)
    return "\n\n".join(" ".join(sentences) for sentences in paragraphs)


def _truncate(text: str) -> str:
    """Cắt còn ≤ 600 ký tự tại ranh giới từ; kết quả vẫn là chuỗi con của `text`."""
    if len(text) <= MAX_QUOTE_CHARS:
        return text
    cut = text.rfind(" ", 0, MAX_QUOTE_CHARS + 1)
    return text[:cut] if cut >= MIN_QUOTE_CHARS else text[:MAX_QUOTE_CHARS]


def default_quote(units: list[_Unit]) -> str | None:
    """Câu đầu tiên đủ 8 ký tự, ưu tiên câu không phải tiêu đề; không có thì `None`."""
    for prefer_body in (True, False):
        for unit in units:
            if (not prefer_body or not unit.is_heading) and len(unit.text) >= MIN_QUOTE_CHARS:
                return _truncate(unit.text)
    return None


def chunk_id_for(doc_id: str, ordinal: int, raw_text: str) -> str:
    digest = hashlib.sha256(f"{doc_id}\n{ordinal}\n{raw_text}".encode()).hexdigest()
    return f"chk_{digest[:16]}"


def build_chunks(document: ConfirmedDocumentText) -> list[KnowledgeChunk]:
    """Cắt văn bản đã xác nhận thành chunk với nhãn khởi đầu của `document` (`label_status = "proposed"`)."""
    chunks: list[KnowledgeChunk] = []
    for units in _pack(_sections(_paragraphs(document))):
        token_count = sum(unit.tokens for unit in units)
        quote = default_quote(units)
        if token_count == 0 or quote is None:
            continue
        raw_text = _raw_text(units)
        first_page, last_page = units[0].page_number, units[-1].page_number
        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id_for(document.doc_id, len(chunks), raw_text),
                doc_id=document.doc_id,
                subject_code=document.subject_code,
                chapter_code=document.chapter_code,
                page_number=first_page,
                page_end=last_page if last_page != first_page else None,
                raw_text=raw_text,
                verbatim_quote=quote,
                category=document.category,
                topic_keys=list(document.topic_keys),
                token_count=token_count,
            )
        )
    return chunks
