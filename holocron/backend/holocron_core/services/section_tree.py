"""Cây tiêu đề và nối trang của MỘT tài liệu (ADR-010 mục 4, 15; spec §5.15; brief X08).

Bước SAU bộ dựng khối: nhận khối đã dựng của mọi trang, trả cây `Section` và bảng `block_id → section_id` (lá chứa
khối). Hàm thuần, tất định; chỉ dùng hình học (cỡ chữ, độ đậm) và đánh số, mẫu chữ lấy từ `LangPatterns`.

Quy tắc chốt:
- Cấp của tiêu đề có đánh số (`1.`, `1.2.3`, `I.`) = độ sâu số; `a)` = con của mục đang mở (anh em của `b)` kế tiếp).
  Tiêu đề không đánh số lấy cấp từ cụm cỡ chữ/độ đậm của TOÀN tài liệu (không ngưỡng tuyệt đối): cụm cỡ lớn hơn = cấp
  nhỏ hơn; cụm có số thứ tự thì lấy độ sâu phổ biến nhất của các mục đánh số trong cụm. Hai cụm cỡ quá gần ⇒ cờ
  `level_uncertain`, không đoán. Không đo được cỡ và không đánh số ⇒ cấp 1 + `level_uncertain`.
- Cấp nhảy (1 → 1.2.3) không tạo nút ảo: nút gắn vào tổ tiên gần nhất có cấp thấp hơn, `level` giữ cấp danh nghĩa.
- Nối trang: tiêu đề trùng (NFC + casefold + gộp khoảng trắng, đã bỏ hậu tố "(tiếp)"…) với một mục đang mở mà mục đó kết
  thúc ở trang liền trước, hoặc tiêu đề có hậu tố nối trang trùng một mục đang mở ⇒ cùng một Section, cờ
  `continued_across_pages`, `heading_block_ids` giữ mọi khối tiêu đề lặp.
- Khối trước tiêu đề đầu tiên thuộc gốc chương (không tạo Section `untitled` riêng). Gốc có `page_start`/`page_end` bao
  mọi trang của tài liệu; các mục khác chỉ bao trang của khối của chính nó.
- Tiêu đề rỗng sau khi bỏ hậu tố ⇒ `title = None` + `untitled`; không đoán tên.
- `section_id` = `sec_` + 16 hex của sha256(document_id, đường dẫn thứ tự tổ tiên, thứ tự tạo).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.blocks import Block, Section, SectionFlag, WordRef
from ..domain.documents import GeoFont, GeoLine, GeoWord, PageGeometry
from ..domain.schemas import StrictModel
from ..infrastructure.lang_patterns import LangPatterns
from .block_pipeline import PageContext

SECTION_TREE_STAGE_VERSION = 1
MAX_SECTION_TITLE_CHARS = 300
SECTION_FLAG_ORDER: tuple[SectionFlag, ...] = ("untitled", "continued_across_pages", "level_uncertain")
_MAX_LEVEL = 6

NumberKind = Literal["arabic", "roman", "letter"]


class SectionTreeSettings(BaseModel):
    """Ngưỡng và mẫu đánh số (ADR-010 mục 15: vào `fingerprint()`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Ba mẫu đánh số đầu tiêu đề; nhóm có tên `num` là phần số. Chỉ chữ số, La Mã, chữ cái đơn.
    arabic_pattern: str = r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,5})[.)]?\s+\S"
    roman_pattern: str = r"^(?P<num>[IVXLC]{1,6})[.)]\s+\S"
    letter_pattern: str = r"^(?P<num>[a-z])\)\s+\S"
    # Hai cỡ chữ lệch tương đối ≤ ngưỡng này (so với cỡ lớn nhất của cụm) thuộc cùng một cụm.
    cluster_tol: float = Field(default=0.04, ge=0.0)
    # Hai cụm liền kề cách nhau (tương đối) < ngưỡng này ⇒ `level_uncertain`.
    close_cluster_tol: float = Field(default=0.12, ge=0.0)
    size_round_digits: int = Field(default=1, ge=0, le=4)

    @field_validator("arabic_pattern", "roman_pattern", "letter_pattern")
    @classmethod
    def _compiles_with_num(cls, value: str) -> str:
        try:
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"mẫu đánh số không biên dịch được: {exc}") from exc
        if "num" not in compiled.groupindex:
            raise ValueError("mẫu đánh số phải có nhóm có tên `num`")
        return value

    @model_validator(mode="after")
    def _tolerances_ordered(self) -> SectionTreeSettings:
        if self.close_cluster_tol < self.cluster_tol:
            raise ValueError("close_cluster_tol phải ≥ cluster_tol")
        return self


_DEFAULT_SETTINGS = SectionTreeSettings()


class SectionTree(StrictModel):
    """Cây tiêu đề của một tài liệu; `sections` theo `ordinal` (gốc đầu tiên)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sections: list[Section]
    block_section: dict[str, str]

    @model_validator(mode="after")
    def _refs_resolve(self) -> SectionTree:
        ids = {section.section_id for section in self.sections}
        if len(ids) != len(self.sections):
            raise ValueError("section_id trùng")
        if not set(self.block_section.values()) <= ids:
            raise ValueError("block_section trỏ tới Section không có")
        return self


@dataclass(frozen=True)
class _Head:
    size: float | None
    bold: bool
    kind: NumberKind | None
    depth: int | None
    title: str
    key: str
    has_suffix: bool


@dataclass
class _Node:
    ordinal: int
    level: int
    kind: NumberKind | None
    path: tuple[int, ...]
    section_id: str
    parent_id: str | None
    title: str | None
    key: str
    flags: set[SectionFlag] = field(default_factory=set)
    heading_ids: list[str] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    heading_pages: list[int] = field(default_factory=list)

    def last_page_before(self, page: int) -> int:
        """Trang lớn nhất < `page` có khối của mục (0 nếu không có)."""
        return max((p for p in self.pages if p < page), default=0)


def _collapse(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _section_id(document_id: str, path: tuple[int, ...], ordinal: int) -> str:
    raw = f"{document_id}\x1f{'/'.join(map(str, path))}\x1f{ordinal}".encode()
    return "sec_" + hashlib.sha256(raw).hexdigest()[:16]


def _majority(items: Sequence[tuple[Any, int]]) -> Any:
    """Giá trị (khác None) có tổng trọng số lớn nhất; hoà ⇒ giá trị nhỏ nhất theo `sorted` của chuỗi `repr`."""
    totals: Counter[Any] = Counter()
    for value, weight in items:
        if value is not None:
            totals[value] += weight
    if not totals:
        return None
    best = max(totals.values())
    return min((v for v, n in totals.items() if n == best), key=repr)


def _measure(block: Block, geometry: PageGeometry, digits: int) -> tuple[float | None, bool]:
    """Cỡ chữ (pt) và độ đậm của khối heading, đo từ các từ `WordRef` của nó trong `geometry`."""
    sizes: list[tuple[float | None, int]] = []
    bolds: list[tuple[bool | None, int]] = []
    fonts: list[GeoFont] = geometry.fonts
    for ref in block.line_refs:
        if not isinstance(ref, WordRef) or ref.line_idx >= len(geometry.lines):
            continue
        line: GeoLine = geometry.lines[ref.line_idx]
        if ref.word_idx >= len(line.words):
            continue
        word: GeoWord = line.words[ref.word_idx]
        weight = sum(1 for c in word.text if not c.isspace())
        size = word.font_size_pt if word.font_size_pt is not None else line.font_size_pt
        font_id = word.font_id if word.font_id is not None else line.font_id
        sizes.append((None if size is None else round(size, digits), weight))
        bolds.append((fonts[font_id].bold if font_id is not None and font_id < len(fonts) else None, weight))
    size_value = _majority(sizes)
    return size_value, _majority(bolds) is True


class _Numbering:
    def __init__(self, settings: SectionTreeSettings) -> None:
        self.arabic = re.compile(settings.arabic_pattern)
        self.roman = re.compile(settings.roman_pattern)
        self.letter = re.compile(settings.letter_pattern)

    def classify(self, text: str) -> tuple[NumberKind | None, int | None]:
        if match := self.arabic.match(text):
            return "arabic", len(match.group("num").rstrip(".").split("."))
        if self.roman.match(text):
            return "roman", 1
        if self.letter.match(text):
            return "letter", None
        return None, None


def _strip_suffix(text: str, suffixes: Sequence[str]) -> tuple[str, bool]:
    folded = text.casefold()
    for suffix in sorted(suffixes, key=len, reverse=True):
        needle = _collapse(suffix).casefold()
        if needle and folded.endswith(needle):
            return text[: len(text) - len(needle)].strip(), True
    return text, False


def _head_info(block: Block, context: PageContext, numbering: _Numbering, settings: SectionTreeSettings) -> _Head:
    size, bold = _measure(block, context.geometry, settings.size_round_digits)
    text = _collapse(block.text or "")
    stripped, has_suffix = _strip_suffix(text, context.lang.continuation_suffixes)
    kind, depth = numbering.classify(stripped)
    title = stripped[:MAX_SECTION_TITLE_CHARS].strip()
    return _Head(size, bold, kind, depth, title, title.casefold(), has_suffix)


@dataclass(frozen=True)
class _Styles:
    """Phân cụm cỡ chữ/độ đậm toàn tài liệu: khoá kiểu → (hạng 1-based, cấp theo đánh số hoặc None, không chắc)."""

    rank: dict[tuple[int, int], int]
    numbered_level: dict[tuple[int, int], int]
    uncertain_clusters: frozenset[int]
    cluster_of: dict[float, int]

    def key_of(self, head: _Head) -> tuple[int, int] | None:
        if head.size is None:
            return None
        return self.cluster_of[head.size], 0 if head.bold else 1


def _cluster_styles(heads: Sequence[_Head], settings: SectionTreeSettings) -> _Styles:
    sizes = sorted({h.size for h in heads if h.size is not None}, reverse=True)
    clusters: list[list[float]] = []
    for size in sizes:
        if clusters and (clusters[-1][0] - size) <= settings.cluster_tol * clusters[-1][0]:
            clusters[-1].append(size)
        else:
            clusters.append([size])
    cluster_of = {size: idx for idx, members in enumerate(clusters) for size in members}
    uncertain: set[int] = set()
    for idx in range(len(clusters) - 1):
        upper, lower = min(clusters[idx]), max(clusters[idx + 1])
        if (upper - lower) < settings.close_cluster_tol * upper:
            uncertain |= {idx, idx + 1}
    styles = _Styles({}, {}, frozenset(uncertain), cluster_of)
    keys = sorted({k for h in heads if (k := styles.key_of(h)) is not None})
    rank = {key: pos + 1 for pos, key in enumerate(keys)}
    depths: dict[tuple[int, int], Counter[int]] = {}
    for head in heads:
        key = styles.key_of(head)
        if key is not None and head.kind in ("arabic", "roman") and head.depth is not None:
            depths.setdefault(key, Counter())[head.depth] += 1
    numbered = {key: min(counter.items(), key=lambda item: (-item[1], item[0]))[0] for key, counter in depths.items()}
    return _Styles(rank, numbered, frozenset(uncertain), cluster_of)


def build_section_tree(
    document_id: str,
    chapter_code: str | None,
    chapter_title: str | None,
    pages: Sequence[tuple[PageContext, Sequence[Block]]],
    lang: LangPatterns,
    settings: SectionTreeSettings = _DEFAULT_SETTINGS,
) -> SectionTree:
    """Dựng cây `Section` của tài liệu từ khối đã dựng của mọi trang (hàm thuần, tất định).

    `lang` dùng cho hậu tố nối trang; `PageContext.lang` của từng trang cũng được tôn trọng khi đo tiêu đề.
    """
    numbering = _Numbering(settings)
    ordered = sorted(pages, key=lambda item: item[0].page_number)
    sequence: list[tuple[PageContext, Block]] = []
    for context, blocks in ordered:
        sequence.extend((context, b) for b in sorted(blocks, key=lambda b: (b.reading_order, b.block_id)))
    infos: dict[str, _Head] = {}
    for context, block in sequence:
        if block.type == "heading":
            infos[block.block_id] = _head_info(block, _with_lang(context, lang), numbering, settings)
    styles = _cluster_styles(list(infos.values()), settings)

    root_title = _collapse(chapter_title)[:MAX_SECTION_TITLE_CHARS].strip() if chapter_title else ""
    root = _Node(0, 0, None, (), _section_id(document_id, (), 0), None, root_title or None, root_title.casefold())
    if not root_title:
        root.flags.add("untitled")
    nodes: list[_Node] = [root]
    stack: list[_Node] = [root]
    block_section: dict[str, str] = {}

    for context, block in sequence:
        page = context.page_number
        if block.type == "heading":
            head = infos[block.block_id]
            target = _continuation_target(stack, head, page)
            if target is not None:
                del stack[stack.index(target) + 1 :]
                target.flags.add("continued_across_pages")
            else:
                level, uncertain = _level_of(head, stack, styles)
                while stack[-1].level >= level:
                    stack.pop()
                parent = stack[-1]
                path = (*parent.path, parent.ordinal)
                ordinal = len(nodes)
                target = _Node(
                    ordinal,
                    level,
                    head.kind,
                    path,
                    _section_id(document_id, path, ordinal),
                    parent.section_id,
                    head.title or None,
                    head.key,
                )
                if not head.title:
                    target.flags.add("untitled")
                if uncertain:
                    target.flags.add("level_uncertain")
                nodes.append(target)
                stack.append(target)
            target.heading_ids.append(block.block_id)
            target.heading_pages.append(page)
        else:
            target = stack[-1]
        target.block_ids.append(block.block_id)
        target.pages.append(page)
        block_section[block.block_id] = target.section_id

    all_pages = [context.page_number for context, _ in ordered]
    sections: list[Section] = []
    for node in nodes:
        is_root = node.parent_id is None
        pages_span = all_pages if is_root else node.pages
        sections.append(
            Section(
                section_id=node.section_id,
                document_id=document_id,
                parent_id=node.parent_id,
                kind="chapter" if is_root else "section",
                level=node.level,
                chapter_code=chapter_code if is_root else None,
                title=node.title,
                heading_block_ids=node.heading_ids,
                block_ids=node.block_ids,
                ordinal=node.ordinal,
                page_start=min(pages_span) if pages_span else 1,
                page_end=max(pages_span) if pages_span else 1,
                flags=[flag for flag in SECTION_FLAG_ORDER if flag in node.flags],
            )
        )
    return SectionTree(sections=sections, block_section=block_section)


def _with_lang(context: PageContext, lang: LangPatterns) -> PageContext:
    """Ngữ cảnh trang với mẫu ngôn ngữ do người gọi truyền (một nguồn cho cả tài liệu)."""
    return PageContext(
        context.document_id,
        context.page_number,
        context.geometry,
        context.extraction,
        lang,
        context.page_number_settings,
    )


def _continuation_target(stack: Sequence[_Node], head: _Head, page: int) -> _Node | None:
    if not head.key:
        return None
    for node in reversed(stack[1:]):
        prior = node.last_page_before(page)
        if node.key != head.key or not prior or page in node.heading_pages:
            continue
        if head.has_suffix or prior >= page - 1:
            return node
    return None


def _level_of(head: _Head, stack: Sequence[_Node], styles: _Styles) -> tuple[int, bool]:
    """Cấp danh nghĩa của tiêu đề mới và cờ `level_uncertain`."""
    key = styles.key_of(head)
    uncertain = key is not None and key[0] in styles.uncertain_clusters
    level: int
    if head.kind == "letter":
        top = stack[-1]
        level = top.level if top.kind == "letter" else top.level + 1
    elif head.depth is not None:
        level = head.depth
    elif key is not None:
        level = styles.numbered_level.get(key, styles.rank[key])
    else:
        level, uncertain = 1, True
    return min(max(level, 1), _MAX_LEVEL), uncertain


class SectionTreeStage:
    """Bọc `build_section_tree` kèm `fingerprint()` (ADR-010 mục 15; mẫu `BaseBlockStage.fingerprint`)."""

    name: str = "section_tree"

    def __init__(self, lang: LangPatterns, settings: SectionTreeSettings | None = None) -> None:
        self.lang = lang
        self.settings = settings or SectionTreeSettings()

    def fingerprint(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": SECTION_TREE_STAGE_VERSION,
            "settings": self.settings.model_dump(),
            "lang_sha256": self.lang.sha256(),
        }

    def run(
        self,
        document_id: str,
        chapter_code: str | None,
        chapter_title: str | None,
        pages: Sequence[tuple[PageContext, Sequence[Block]]],
    ) -> SectionTree:
        return build_section_tree(document_id, chapter_code, chapter_title, pages, self.lang, self.settings)
