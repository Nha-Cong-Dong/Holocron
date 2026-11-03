"""Cắt chunk tất định từ văn bản đã xác nhận (SPECIFICATION §12.1, ADR-006 mục 3). LLM không cắt chunk.

Thuật toán (cùng đầu vào ⇒ cùng tập chunk, cùng `chunk_id`):

1. **Đoạn.** Mỗi dòng được gộp khoảng trắng. Dòng rỗng, dòng tiêu đề và hết trang là ranh giới đoạn; các dòng liền
   nhau trong một đoạn nối bằng một dấu cách.
2. **Phần theo tiêu đề.** Mỗi dòng tiêu đề mở một phần mới. Phần chỉ có tiêu đề (tiêu đề nằm ngay trên một tiêu đề
   khác) được gộp vào phần sau nó. Dòng tiêu đề nhận theo mẫu không phụ thuộc môn: tiêu đề Markdown (`# …`), từ khoá
   đánh số (`Chương 3`, `Chapter 4`, …; danh sách từ khoá ở `infrastructure/lang/*.toml`) hoặc số mục nhiều cấp (`3.2 …`, `3.2.1 …`);
   dòng dài quá 120 ký tự hoặc kết thúc bằng dấu câu không phải tiêu đề.
3. **Giới hạn 450 token.** Token là token âm tiết của chỉ mục tìm kiếm (`domain.search_text.search_tokens`). Phần
   dài hơn được tách tại ranh giới đoạn; đoạn không vừa thì tách tại ranh giới câu; câu dài hơn 450 token thì tách
   theo từ; một "từ" (không có dấu cách) dài hơn 450 token, ví dụ `a-b-c-…`, thì cắt cứng theo token, ưu tiên cắt
   ngay trước một token. Các mảnh của cùng một từ được nối lại không có dấu cách khi nằm chung chunk, nên `raw_text`
   luôn là đoạn liền của văn bản đã xác nhận. Chunk sau mở đầu bằng câu cuối của chunk trước (chồng lấn 1 câu), trừ
   khi câu đó cộng với phần tiếp theo vượt giới hạn. Chồng lấn không vượt qua ranh giới phần.
4. **Trích dẫn mặc định** (§5.8, khi chưa có LLM): câu đầu tiên đủ 8 ký tự, ưu tiên câu không phải tiêu đề; dài quá
   600 ký tự thì cắt tại ranh giới từ.
5. **`chunk_id`** = `"chk_"` + 16 ký tự hex đầu của sha256 trên `doc_id`, thứ tự chunk và `raw_text`.

Chunk không có token nào hoặc ngắn hơn 8 ký tự bị bỏ (không đủ để trích dẫn).

**Cắt theo khối** (`build_block_chunks`, tài liệu có khối; ADR-010 mục 16): chunk thuộc MỘT Section, gom khối ứng viên
có chữ theo (trang, `reading_order`) thành chunk ≤ 450 token tại ranh giới khối, không chồng lấn; khối dài hơn tách
theo câu/từ bằng các hàm ở trên. `raw_text` = `text` các khối nối bằng `\\n\\n`. Chunk ngắn hơn 8 ký tự (sau gộp khoảng
trắng) hoặc không có token gộp vào chunk kề cùng Section khi còn vừa giới hạn; không gộp được thì không thành chunk và
các khối của nó được báo `too_short_for_chunk`. Trang không có khối cắt bằng `build_chunks` theo từng dãy trang liền
nhau. MỘT bộ đếm `ordinal` chung theo (trang đầu, `reading_order` của khối đầu); chunk của một dãy trang không có khối
đứng ở vị trí trang đầu của dãy.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.schemas import KnowledgeChunk, normalize_quote
from ..domain.search_text import TOKEN_RE, search_tokens
from ..domain.vault import BlockDocumentText, ChunkBlock, ConfirmedDocumentText, ConfirmedPage
from ..infrastructure.lang_patterns import load_lang_patterns

MAX_CHUNK_TOKENS = 450
MAX_HEADING_CHARS = 120
MIN_QUOTE_CHARS = 8
MAX_QUOTE_CHARS = 600

# Mẫu tiêu đề không phụ thuộc ngôn ngữ: tiêu đề Markdown và số mục nhiều cấp. Từ khoá đánh số (`Chương`, `Chapter`,
# …) nằm ở `infrastructure/lang/*.toml` (`LangPatterns.heading_keywords`, ADR-010 mục 16), không nằm trong code.
_MARKDOWN_HEADING = r"#{1,6}\s+\S"
_NUMBERED_HEADING = r"[0-9]{1,2}(?:\.[0-9]{1,2})+\.?\s+\S"


def _heading_re(keywords: Sequence[str]) -> re.Pattern[str]:
    """Biểu thức tiêu đề của bước 2 với các từ khoá đánh số `keywords` (so không phân biệt hoa thường)."""
    alternatives = [_MARKDOWN_HEADING, _NUMBERED_HEADING]
    if keywords:
        ordered = sorted(set(keywords), key=lambda keyword: (-len(keyword), keyword))
        words = "|".join(re.escape(keyword) for keyword in ordered)
        alternatives.insert(1, rf"(?:{words})\s+(?:[0-9]+|[ivxlc]+)\b")
    return re.compile(r"^(?:" + "|".join(alternatives) + r")", re.IGNORECASE)


# Từ khoá mặc định: nạp MỘT lần lúc import từ các tệp ngôn ngữ đóng gói, thành hằng bất biến.
DEFAULT_HEADING_KEYWORDS: tuple[str, ...] = load_lang_patterns().heading_keywords
HEADING_RE = _heading_re(DEFAULT_HEADING_KEYWORDS)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
HEADING_TRAILING_PUNCTUATION = ".,;:!?…"


@dataclass(frozen=True)
class _Paragraph:
    page_number: int
    text: str
    is_heading: bool


@dataclass(frozen=True)
class _Unit:
    """Một câu (hoặc mảnh câu) — đơn vị nhỏ nhất khi tách chunk.

    `glued`: mảnh nối tiếp một từ bị cắt cứng, nên đứng liền (không dấu cách) sau đơn vị ngay trước nó.
    """

    paragraph: int
    page_number: int
    text: str
    tokens: int
    is_heading: bool
    glued: bool = False


def _pattern_for(heading_keywords: Sequence[str] | None) -> re.Pattern[str]:
    return HEADING_RE if heading_keywords is None else _heading_re(heading_keywords)


def _matches_heading(line: str, pattern: re.Pattern[str]) -> bool:
    if not line or len(line) > MAX_HEADING_CHARS or line[-1] in HEADING_TRAILING_PUNCTUATION:
        return False
    return pattern.match(line) is not None


def is_heading(line: str, heading_keywords: Sequence[str] | None = None) -> bool:
    """Dòng (đã gộp khoảng trắng) có phải tiêu đề theo mẫu ở bước 2 không.

    `heading_keywords` None ⇒ từ khoá của các tệp ngôn ngữ đóng gói (`DEFAULT_HEADING_KEYWORDS`).
    """
    return _matches_heading(line, _pattern_for(heading_keywords))


def _flush(buffer: list[str], page_number: int, paragraphs: list[_Paragraph]) -> None:
    if buffer:
        paragraphs.append(_Paragraph(page_number, " ".join(buffer), is_heading=False))
        buffer.clear()


def _paragraphs(document: ConfirmedDocumentText, pattern: re.Pattern[str]) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    for page in document.pages:
        buffer: list[str] = []
        for raw_line in page.lines:
            line = normalize_quote(raw_line)
            if not line:
                _flush(buffer, page.page_number, paragraphs)
            elif _matches_heading(line, pattern):
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


def _longest_fitting_prefix(text: str) -> int:
    """Độ dài lớn nhất `n ≥ 1` mà `text[:n]` có ≤ 450 token (tìm nhị phân; `text` có hơn 450 token).

    Bất biến `_token_count(text[:low]) ≤ 450` luôn đúng, nên kết quả luôn vừa giới hạn kể cả khi chuẩn hoá (NFC,
    `casefold`) làm số token không tăng đều theo độ dài; một ký tự cho tối đa vài token nên `low = 1` vừa.
    """
    low, high = 1, len(text)
    while high - low > 1:
        middle = (low + high) // 2
        if _token_count(text[:middle]) <= MAX_CHUNK_TOKENS:
            low = middle
        else:
            high = middle
    return low


def _hard_cut(word: str) -> list[str]:
    """Cắt một từ (không có dấu cách) có hơn 450 token thành các mảnh liền nhau, mỗi mảnh ≤ 450 token.

    Mỗi mảnh dài nhất có thể; điểm cắt lùi về đầu token gần nhất (`a-b-|c`) nếu vẫn còn ít nhất một token ở trước,
    ngược lại cắt đúng tại ký tự (một token dài hơn 450 token sau chuẩn hoá, ví dụ chuỗi `İ`).
    """
    pieces: list[str] = []
    rest = word
    while _token_count(rest) > MAX_CHUNK_TOKENS:
        cut = _longest_fitting_prefix(rest)
        starts = [match.start() for match in TOKEN_RE.finditer(rest, 0, cut + 1) if 0 < match.start() <= cut]
        if starts and 0 < _token_count(rest[: starts[-1]]) <= MAX_CHUNK_TOKENS:
            cut = starts[-1]
        pieces.append(rest[:cut])
        rest = rest[cut:]
    pieces.append(rest)
    return pieces


def _split_long_sentence(sentence: str) -> list[tuple[str, bool]]:
    """Câu dài hơn giới hạn được tách theo từ thành các mảnh ≤ 450 token; từ quá dài thì cắt cứng (`_hard_cut`).

    Trả `(mảnh, glued)`: `glued` khi mảnh mở đầu bằng phần sau của một từ bị cắt (đứng liền sau mảnh trước).
    """
    pieces: list[tuple[str, bool]] = []
    words: list[str] = []
    glued = False
    count = 0
    for word in sentence.split(" "):
        word_tokens = _token_count(word)
        if word_tokens > MAX_CHUNK_TOKENS:
            if words:
                pieces.append((" ".join(words), glued))
            parts = _hard_cut(word)
            pieces.extend((part, position > 0) for position, part in enumerate(parts[:-1]))
            words, count, glued = [parts[-1]], _token_count(parts[-1]), True
            continue
        if words and count + word_tokens > MAX_CHUNK_TOKENS:
            pieces.append((" ".join(words), glued))
            words, count, glued = [], 0, False
        words.append(word)
        count += word_tokens
    if words:
        pieces.append((" ".join(words), glued))
    return pieces


def _units(index: int, paragraph: _Paragraph) -> list[_Unit]:
    units: list[_Unit] = []
    for sentence in SENTENCE_SPLIT_RE.split(paragraph.text):
        pieces = [(sentence, False)] if _token_count(sentence) <= MAX_CHUNK_TOKENS else _split_long_sentence(sentence)
        units.extend(
            _Unit(index, paragraph.page_number, piece, _token_count(piece), paragraph.is_heading, glued)
            for piece, glued in pieces
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
            paragraphs.append([unit.text])
            previous = unit.paragraph
        elif unit.glued:
            paragraphs[-1][-1] += unit.text
        else:
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


@dataclass(frozen=True)
class BlockChunks:
    """Kết quả cắt theo khối: chunk theo thứ tự `ordinal` và các khối không thành chunk vì quá ngắn (sắp, không trùng)."""

    chunks: list[KnowledgeChunk]
    too_short_block_ids: list[str]


@dataclass(frozen=True)
class _Segment:
    """Một khối, hoặc một mảnh (câu/từ) của khối dài hơn 450 token — đơn vị gom của cắt theo khối."""

    block: ChunkBlock
    text: str
    tokens: int
    glued: bool = False


def _block_segments(block: ChunkBlock) -> list[_Segment]:
    tokens = _token_count(block.text)
    if tokens <= MAX_CHUNK_TOKENS:
        return [_Segment(block, block.text, tokens)]
    segments: list[_Segment] = []
    for sentence in SENTENCE_SPLIT_RE.split(block.text):
        pieces = [(sentence, False)] if _token_count(sentence) <= MAX_CHUNK_TOKENS else _split_long_sentence(sentence)
        segments.extend(_Segment(block, piece, _token_count(piece), glued) for piece, glued in pieces)
    return segments


def _section_runs(blocks: Sequence[ChunkBlock]) -> list[list[ChunkBlock]]:
    """Các dãy khối liền nhau (theo trang, `reading_order`) cùng một Section."""
    runs: list[list[ChunkBlock]] = []
    for block in sorted(blocks, key=lambda b: (b.page_number, b.reading_order, b.block_id)):
        if runs and runs[-1][-1].section_id == block.section_id:
            runs[-1].append(block)
        else:
            runs.append([block])
    return runs


def _pack_segments(segments: list[_Segment]) -> list[list[_Segment]]:
    """Gom tham lam ≤ 450 token, không chồng lấn."""
    packed: list[list[_Segment]] = []
    current: list[_Segment] = []
    total = 0
    for segment in segments:
        if current and total + segment.tokens > MAX_CHUNK_TOKENS:
            packed.append(current)
            current, total = [], 0
        current.append(segment)
        total += segment.tokens
    if current:
        packed.append(current)
    return packed


def _segments_text(segments: Sequence[_Segment]) -> str:
    """`text` các khối nối bằng `\\n\\n`; mảnh của cùng một khối nối bằng dấu cách (liền nhau nếu `glued`)."""
    blocks: list[str] = []
    previous: str | None = None
    for segment in segments:
        if segment.block.block_id != previous:
            blocks.append(segment.text)
            previous = segment.block.block_id
        elif segment.glued:
            blocks[-1] += segment.text
        else:
            blocks[-1] += " " + segment.text
    return "\n\n".join(blocks)


def _is_short(segments: Sequence[_Segment]) -> bool:
    return sum(s.tokens for s in segments) == 0 or len(normalize_quote(_segments_text(segments))) < MIN_QUOTE_CHARS


def _fits(segments: Sequence[_Segment]) -> bool:
    return sum(s.tokens for s in segments) <= MAX_CHUNK_TOKENS


def _merge_short(packed: list[list[_Segment]]) -> tuple[list[list[_Segment]], list[str]]:
    """Gộp chunk ngắn vào chunk kề TRƯỚC, không vừa thì vào chunk kề SAU (cùng Section, còn ≤ 450 token); không gộp
    được ⇒ bỏ chunk và trả các khối của nó."""
    kept: list[list[_Segment]] = []
    dropped: list[str] = []
    carry: list[_Segment] | None = None
    for chunk in packed:
        if carry is not None:
            if _fits(carry + chunk):
                chunk = carry + chunk
            else:
                dropped.extend(segment.block.block_id for segment in carry)
            carry = None
        if not _is_short(chunk):
            kept.append(chunk)
        elif kept and _fits(kept[-1] + chunk):
            kept[-1] = kept[-1] + chunk
        else:
            carry = chunk
    if carry is not None:
        dropped.extend(segment.block.block_id for segment in carry)
    return kept, dropped


def _block_quote(segments: Sequence[_Segment], raw_text: str) -> str:
    """`default_quote` trên các câu của chunk (ưu tiên khối không phải tiêu đề); không câu nào đủ 8 ký tự thì lấy
    `raw_text` đã gộp khoảng trắng (cắt ≤ 600 ký tự tại ranh giới từ) — vẫn là chuỗi con sau chuẩn hoá (I3)."""
    units = [
        _Unit(index, segment.block.page_number, sentence, 0, segment.block.is_heading)
        for index, segment in enumerate(segments)
        for sentence in SENTENCE_SPLIT_RE.split(segment.text)
    ]
    return default_quote(units) or _truncate(normalize_quote(raw_text))


def _block_chunk(document: BlockDocumentText, segments: Sequence[_Segment]) -> KnowledgeChunk:
    raw_text = _segments_text(segments)
    first, last = segments[0].block.page_number, segments[-1].block.page_number
    block_ids = list(dict.fromkeys(segment.block.block_id for segment in segments))
    return KnowledgeChunk(
        chunk_id=chunk_id_for(document.doc_id, 0, raw_text),
        doc_id=document.doc_id,
        subject_code=document.subject_code,
        chapter_code=document.chapter_code,
        page_number=first,
        page_end=last if last != first else None,
        raw_text=raw_text,
        verbatim_quote=_block_quote(segments, raw_text),
        category=document.category,
        topic_keys=list(document.topic_keys),
        token_count=sum(segment.tokens for segment in segments),
        section_id=segments[0].block.section_id,
        block_ids=block_ids,
    )


def _page_runs(pages: Sequence[ConfirmedPage]) -> list[list[ConfirmedPage]]:
    """Các dãy trang liền nhau (số trang liên tiếp)."""
    runs: list[list[ConfirmedPage]] = []
    for page in pages:
        if runs and runs[-1][-1].page_number + 1 == page.page_number:
            runs[-1].append(page)
        else:
            runs.append([page])
    return runs


def build_block_chunks(document: BlockDocumentText, heading_keywords: Sequence[str] | None = None) -> BlockChunks:
    """Cắt chunk cho tài liệu có khối (ADR-010 mục 16; xem docstring module). Hàm thuần, tất định: cùng đầu vào ⇒ cùng
    chunk và cùng `chunk_id = chunk_id_for(doc_id, ordinal, raw_text)`. `heading_keywords` dùng cho `build_chunks` của
    trang không có khối (None ⇒ từ khoá của các tệp ngôn ngữ đóng gói)."""
    keyed: list[tuple[tuple[int, int, int], KnowledgeChunk]] = []
    too_short: list[str] = []
    for run in _section_runs(document.blocks):
        packed, dropped = _merge_short(_pack_segments([s for block in run for s in _block_segments(block)]))
        too_short.extend(dropped)
        for segments in packed:
            first = segments[0].block
            keyed.append(((first.page_number, first.reading_order, len(keyed)), _block_chunk(document, segments)))
    for pages in _page_runs(document.line_pages):
        for chunk in build_chunks(document.labels(pages), heading_keywords):
            keyed.append(((pages[0].page_number, -1, len(keyed)), chunk))
    chunks = [
        chunk.model_copy(update={"chunk_id": chunk_id_for(document.doc_id, ordinal, chunk.raw_text)})
        for ordinal, (_, chunk) in enumerate(sorted(keyed, key=lambda item: item[0]))
    ]
    return BlockChunks(chunks=chunks, too_short_block_ids=sorted(set(too_short)))


def build_chunks(
    document: ConfirmedDocumentText, heading_keywords: Sequence[str] | None = None
) -> list[KnowledgeChunk]:
    """Cắt văn bản đã xác nhận thành chunk với nhãn khởi đầu của `document` (`label_status = "proposed"`).

    `heading_keywords` None ⇒ từ khoá tiêu đề của các tệp ngôn ngữ đóng gói (`DEFAULT_HEADING_KEYWORDS`).
    """
    chunks: list[KnowledgeChunk] = []
    for units in _pack(_sections(_paragraphs(document, _pattern_for(heading_keywords)))):
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
