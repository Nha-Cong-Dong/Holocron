"""Interface lưu trữ sau `Protocol` (SPECIFICATION §5.13; CLAUDE.md §2.2).

Router và service chỉ phụ thuộc các `Protocol` ở đây; bản in-memory (`infrastructure/memory_store.py`, cho test và
chế độ dev) và bản SQLite (`infrastructure/storage/`) cùng thoả chúng. Lỗi lưu trữ là exception của domain để tầng
trên đổi thành mã HTTP mà không biết kho cụ thể: `VersionConflictError` và `ConcurrentUpdateError` ⇒ 409,
`RecordNotFoundError` ⇒ 404, `MissingReferenceError` ⇒ 422, `RecordInUseError` ⇒ 409, `StorageBusyError` ⇒ 503.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from .ai import AiCacheEntry
from .blocks import Block, BlockFlag, CropImage, ExtractionLedger, Section, SectionFlag
from .documents import (
    CHAPTER_CODE_PATTERN,
    CHAPTER_FLAG_ORDER,
    MAX_CHAPTER_TITLE_CHARS,
    ChapterCodeSource,
    ChapterTitleSource,
    PageEditAudit,
    PageExtraction,
    PageGeometry,
    PageSummary,
    StoredChapterFlag,
    normalize_chapter_title,
)
from .ingestion import IngestionItem, UploadedDocument
from .practice import PracticeAttempt, PracticeAttemptPage, ReviewQuestionPage, StoredTwin
from .schemas import BlueprintSummary, DocumentMeta, ExamBlueprint, ExamFormat, KnowledgeChunk, Subject
from .twins import IdempotencyRecord, TwinBatch
from .vault import ConceptPair, ConceptPairStatus, VaultEdit

IDEMPOTENCY_TTL = timedelta(hours=24)
# Trần cache AI (spec §5.16; ADR-009 mục 8): field có mặc định của store, không phải biến môi trường.
AI_CACHE_MAX_ROWS = 20_000
AI_CACHE_TTL_DAYS = 90

DocumentStatus = Literal["processing", "review", "confirmed", "failed"]
PageStatus = Literal["pending", "ok", "needs_review", "failed"]
OutputLanguage = Literal["vi", "en"]
SearchIndex = Literal["primary", "no_diacritics"]
VaultEntity = Literal["chunk", "concept_pair"]


class StorageError(Exception):
    """Gốc của mọi lỗi lưu trữ mà tầng trên cần phân biệt."""


class RecordNotFoundError(StorageError):
    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"không tìm thấy {entity} {key}")
        self.entity = entity
        self.key = key


class VersionConflictError(StorageError):
    """Ghi có điều kiện `WHERE version = expected` không khớp dòng nào trong khi bản ghi vẫn tồn tại (BE-003)."""

    def __init__(self, entity: str, key: str, expected_version: int, current_version: int) -> None:
        super().__init__(f"version lệch: hiện tại {current_version}")
        self.entity = entity
        self.key = key
        self.expected_version = expected_version
        self.current_version = current_version


class ConcurrentUpdateError(StorageError):
    """Version khớp nhưng trạng thái đang lưu khác trạng thái người ghi đã đọc: một lần ghi khác đã chen vào.

    Sửa stem, gắn non-cloneable và approve không tăng `table_primary.version` (§5.13: `ingestion_items.version` bằng
    `table_primary.version`), nên chỉ so version thì hai lần ghi chạy chéo đều khớp và lần sau ghi đè lần trước.
    """

    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"{entity} vừa bị thay đổi bởi một yêu cầu khác, hãy tải lại rồi thử lại")
        self.entity = entity
        self.key = key


class StorageBusyError(StorageError):
    """Kho đang bị tiến trình khác giữ khoá ghi quá `busy_timeout`; thử lại sau."""


class MissingReferenceError(StorageError):
    """Bản ghi trỏ tới một bản ghi khác không tồn tại (khoá ngoại)."""


class RecordInUseError(StorageError):
    """Không xoá được vì còn bản ghi khác tham chiếu (`ON DELETE RESTRICT`)."""


class DocumentExistsError(StorageError):
    """Đã có tài liệu nhập qua §6.5 với cùng `document_id`; `create_imported_document` không ghi gì."""

    def __init__(self, document_id: str) -> None:
        super().__init__(f"tài liệu {document_id} đã được nhập")
        self.document_id = document_id


class SubjectExistsError(StorageError):
    """Đã có môn cùng `subject_code` (kể cả môn tạo ngầm); `create_subject` không ghi gì (§6.8: 409)."""

    def __init__(self, subject_code: str) -> None:
        super().__init__(f"môn {subject_code} đã có")
        self.subject_code = subject_code


class VaultSearchUnavailableError(StorageError):
    """SQLite không có FTS5; tầng gọi dùng BM25 tự cài (§5.13, ADR-006 mục 4)."""


class ImportedDocument(BaseModel):
    """Một dòng `documents` của tài liệu nhập qua bộ nhập PDF (§6.5); upload đề (§6.1) chỉ có `UploadedDocument`.

    Bộ nhập luôn đặt `chapter_code`: người dùng nhập ở form (tuỳ chọn từ 2026-10-31), hoặc code chọn theo §5.7
    "Chương tự động". `None` chỉ gặp ở bản ghi tạo trước khi có cột `documents.chapter_code` (migration 0002).

    Cột của migration 0004 (§5.13): `chapter_code_source` (`None` ở bản ghi không có `chapter_code`; tầng gọi coi
    bản ghi cũ có `chapter_code` mà thiếu nguồn là `"user"`, §5.7), `chapter_title` cùng `chapter_title_source`
    (cùng `None` với bản ghi cũ), `chapter_flags` (sắp theo thứ tự khai báo, không trùng; bản ghi cũ có thể còn cờ `CHAPTER_NUMBER_TAKEN`, giữ
    nguyên) và `auto_confirm`.

    Cột của migration khối (§5.13; ADR-010 mục 13, 17): `layout_version`, `layout_settings_sha256` (cùng null với tài
    liệu chưa có khối lưu), `blocks_after_close` và `import_generation` (thế hệ nhập, nội bộ, KHÔNG ra
    `DocumentImport`; 0 = tài liệu nhập trước migration khối). Bốn field này chỉ do `create_imported_document` (thế
    hệ) và `save_document_layout` (ba field còn lại) ghi; `save_imported_document`/`update_imported_document` giữ
    nguyên giá trị đang lưu, để bản ghi đọc trước một lần lưu khối không ghi đè khối đã lưu.
    """

    meta: DocumentMeta
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1)
    output_language: OutputLanguage = "vi"
    chapter_code: str | None = Field(default=None, pattern=CHAPTER_CODE_PATTERN)
    status: DocumentStatus
    version: int = Field(default=1, ge=1)
    chapter_code_source: ChapterCodeSource | None = None
    chapter_title: str | None = Field(default=None, min_length=1, max_length=MAX_CHAPTER_TITLE_CHARS)
    chapter_title_source: ChapterTitleSource | None = None
    chapter_flags: list[StoredChapterFlag] = Field(default_factory=list)
    auto_confirm: bool = True
    layout_version: int | None = Field(default=None, ge=1)
    layout_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    blocks_after_close: bool = False
    import_generation: int = Field(default=0, ge=0)

    @field_validator("chapter_flags")
    @classmethod
    def _ordered_flags(cls, value: list[StoredChapterFlag]) -> list[StoredChapterFlag]:
        present = set(value)
        return [flag for flag in CHAPTER_FLAG_ORDER if flag in present]

    @model_validator(mode="after")
    def _title_has_a_source(self) -> ImportedDocument:
        if (self.chapter_title is None) != (self.chapter_title_source is None):
            raise ValueError("chapter_title và chapter_title_source cùng null hoặc cùng có giá trị")
        if (self.layout_version is None) != (self.layout_settings_sha256 is None):
            raise ValueError("layout_version và layout_settings_sha256 cùng null hoặc cùng có giá trị")
        return self


class IngestionRepository(Protocol):
    """Item của Ingestion Gate và tài liệu đề tải lên (§5.4, §6.1–§6.4)."""

    def get_item(self, item_id: str) -> IngestionItem | None: ...

    def save_item(self, item: IngestionItem) -> IngestionItem:
        """Ghi không điều kiện (tạo mới hoặc thay toàn bộ). `MissingReferenceError` khi kho kiểm khoá ngoại tài liệu."""
        ...

    def update_item(
        self, item: IngestionItem, expected_version: int, expected: IngestionItem | None = None
    ) -> IngestionItem:
        """So-và-ghi nguyên tử theo `table_primary.version` (§6.2) và, khi có `expected`, theo toàn bộ trạng thái.

        Bản ghi đang lưu phải có version `expected_version`; version mới là `item.table_primary.version`, bằng
        `expected_version` (sửa không đụng bảng) hoặc `expected_version + 1` (sửa bảng). Lệch ⇒
        `VersionConflictError`; không có bản ghi ⇒ `RecordNotFoundError`. `expected` là item người ghi đã đọc: trạng
        thái đang lưu (trừ `audits`) khác nó ⇒ `ConcurrentUpdateError`. Mọi lần đọc-sửa-ghi PHẢI truyền `expected`,
        vì sửa không đụng bảng giữ nguyên version.
        """
        ...

    def save_document(self, document: UploadedDocument) -> UploadedDocument:
        """Cùng nội dung ⇒ cùng `document_id`: giữ bản ghi đầu tiên (idempotent)."""
        ...

    def get_document(self, document_id: str) -> UploadedDocument | None: ...


def check_page_audit(audit: PageEditAudit, document_id: str, page_number: int, expected_version: int) -> None:
    """`audit` thuộc đúng trang `(document_id, page_number)` và bắt đầu từ `expected_version`; sai ⇒ `ValueError`."""
    if (audit.document_id, audit.page_number) != (document_id, page_number):
        raise ValueError("audit không thuộc trang đang ghi")
    if audit.from_version != expected_version:
        raise ValueError(f"audit phải có from_version = {expected_version}")


def same_item_state(stored: IngestionItem, expected: IngestionItem) -> bool:
    """Hai item có cùng trạng thái lưu (mọi field trừ `audits`, vốn chỉ đổi cùng version bảng)."""
    return stored.model_dump(exclude={"audits"}) == expected.model_dump(exclude={"audits"})


class SubjectRepository(Protocol):
    """Môn học (§5.13 bảng `subjects`, §5.14, §6.8). Bảng khác không có khoá ngoại tới môn."""

    def list_subjects(self) -> list[Subject]:
        """Mọi môn, sắp theo `subject_code`."""
        ...

    def get_subject(self, subject_code: str) -> Subject | None: ...

    def create_subject(self, subject: Subject) -> Subject:
        """Tạo môn với `version = 1`; đã có môn cùng mã ⇒ `SubjectExistsError`, không ghi gì."""
        ...

    def update_subject(
        self,
        subject_code: str,
        expected_version: int,
        *,
        display_name: str | None,
        exam_format: ExamFormat | None,
        now: datetime,
    ) -> Subject:
        """So-và-ghi nguyên tử các field khác `None`, `version` tăng 1, `updated_at = now`.

        Không có môn ⇒ `RecordNotFoundError`; version lệch ⇒ `VersionConflictError`.
        """
        ...


class BlueprintRepository(Protocol):
    def save_blueprint(
        self, blueprint: ExamBlueprint, item_ids: Sequence[str] = (), *, implicit_subject: Subject | None = None
    ) -> ExamBlueprint:
        """Lưu blueprint kèm item nguồn (item thứ i ứng với slot i + 1, §5.9) để kiểm lại I5 khi sinh twin.

        `implicit_subject`: môn tạo ngầm (§5.14) khi `blueprint.subject_code` chưa có môn; được tạo trong CÙNG giao dịch
        với blueprint, nên lưu lỗi thì không có môn nào được tạo. Môn đã có thì giữ nguyên.
        """
        ...

    def get_blueprint(self, blueprint_id: str) -> ExamBlueprint | None: ...

    def blueprint_item_ids(self, blueprint_id: str) -> tuple[str, ...]: ...

    def list_blueprint_summaries(self, subject_code: str | None = None) -> list[BlueprintSummary]:
        """Mới nhất trước (theo `created_at`, hoà thì theo `blueprint_id`); `batch_count` = số batch đã lưu (§6.7)."""
        ...


class TwinBatchRepository(Protocol):
    """Batch twin đã sinh, tra theo `Idempotency-Key` trong 24 h (§6.3)."""

    def get_idempotent(self, blueprint_id: str, key: str, now: datetime) -> IdempotencyRecord | None:
        """Bản ghi còn hiệu lực (`now - stored_at <= IDEMPOTENCY_TTL`) của `(blueprint, key)`; hết hạn ⇒ `None`."""
        ...

    def save_batch(self, batch: TwinBatch, idempotency_key: str | None, body_digest: str) -> TwinBatch:
        """API ghi batch duy nhất của luồng sinh twin (§6.3), nguyên tử; trả batch mà request nhận được.

        Mốc thời gian là `batch.created_at` (cũng là `stored_at` của bản ghi idempotency).
        - `idempotency_key` `None` ⇒ ghi batch với khoá `NULL`.
        - Khoá K chưa có bản ghi, hoặc bản ghi của K đã hết hạn TTL ⇒ dọn khoá K cũ (nếu hết hạn) rồi ghi với K.
        - K đang có bản ghi còn hạn với body khác ⇒ ghi batch mới với khoá `NULL`, KHÔNG đụng bản ghi của K.
        - K đang có bản ghi còn hạn với cùng body ⇒ không ghi gì, trả đúng batch cũ (cùng `batch_id`).
        Blueprint không tồn tại ⇒ `MissingReferenceError` (kho SQLite).
        """
        ...

    def save_idempotent(self, blueprint_id: str, key: str, record: IdempotencyRecord) -> None:
        """Ghi bản ghi mới cho `(blueprint, key)`; bản ghi cũ cùng khoá không còn tra được nữa.

        Đường ghi cấp thấp có từ trước `save_batch` (giữ cho test kho); router không dùng.
        """
        ...

    def get_batch_blueprint_id(self, batch_id: str) -> str | None:
        """`blueprint_id` của batch đã lưu; batch không có ⇒ `None` (§6.12 bước 2)."""
        ...

    def get_twins(self, twin_ids: Sequence[str]) -> dict[str, StoredTwin]:
        """Twin đã lưu theo `twin_id` kèm batch, blueprint và môn chứa nó (§5.17); id không có thì vắng trong kết quả."""
        ...


class PracticeAttemptRepository(Protocol):
    """Lượt làm bài và câu sai để ôn (§5.17, §5.13 bảng `practice_attempts`, `practice_answers`).

    Lượt không sửa được sau khi ghi. Mọi số đếm do code đếm trên hàng câu còn lưu lúc đọc; lượt không còn câu nào
    (twin bị xoá theo cascade của batch) bị xoá trong cùng giao dịch. Thứ tự "gần nhất" theo `seq` tăng đơn điệu do
    kho cấp lúc ghi, không theo đồng hồ.
    """

    def save_practice_attempt(self, attempt: PracticeAttempt) -> PracticeAttempt:
        """Ghi lượt và mọi câu của nó trong một giao dịch; `seq` = `MAX(seq) + 1`. Twin hay batch không còn ⇒
        `MissingReferenceError` (kho SQLite)."""
        ...

    def get_practice_attempt(self, attempt_id: str) -> PracticeAttempt | None: ...

    def list_practice_attempts(self, subject_code: str, offset: int, limit: int) -> PracticeAttemptPage:
        """Lượt của môn, mới nhất trước (`seq` giảm dần); `total` là số lượt của môn."""
        ...

    def list_review_questions(self, subject_code: str, offset: int, limit: int) -> ReviewQuestionPage:
        """Câu sai để ôn của môn theo luật §5.17: câu trả lời `correct`/`incorrect` gần nhất của mỗi twin là
        `incorrect`; sắp `seq` giảm dần rồi `ordinal` tăng dần."""
        ...

    def delete_practice_attempt(self, attempt_id: str) -> bool:
        """Xoá một lượt cùng các câu của nó; không có ⇒ `False`."""
        ...

    def delete_practice_attempts(self, subject_code: str) -> int:
        """Xoá mọi lượt của môn; trả số lượt đã xoá (0 khi môn chưa có lượt)."""
        ...


class PageLayout(BaseModel):
    """Khối và sổ bảo toàn của MỘT trang để lưu bằng `save_document_layout` (thay toàn bộ khối/ledger của trang).

    `ledger` None ⇒ trang không còn ledger (ví dụ `LAYOUT_FAILED` vì thiếu geometry).
    """

    page_number: int = Field(ge=1)
    blocks: list[Block] = Field(default_factory=list)
    ledger: ExtractionLedger | None = None

    @model_validator(mode="after")
    def _same_page(self) -> PageLayout:
        if any(block.page_number != self.page_number for block in self.blocks):
            raise ValueError("mọi khối phải thuộc đúng trang page_number")
        if self.ledger is not None and self.ledger.page_number != self.page_number:
            raise ValueError("ledger phải thuộc đúng trang page_number")
        if len({block.block_id for block in self.blocks}) != len(self.blocks):
            raise ValueError("block_id trùng trong một trang")
        return self


class PageRewrite(BaseModel):
    """Một hàng trang ghi lại cùng giao dịch lưu khối (`LAYOUT_FAILED`, bỏ tự xác nhận khi dựng lại; x09c).

    Cột `status`/`confirmed` lấy từ `page`; so-và-ghi theo `expected_version` như `update_page`.
    """

    page: PageExtraction
    expected_version: int = Field(ge=1)


class DocumentLayout(BaseModel):
    """Một lần lưu khối của tài liệu (ADR-010 mục 13 "Lưu hình học"): khối, ledger của các trang trong `pages`,
    `layout_version`/`layout_settings_sha256` của tài liệu, cờ `blocks_after_close` (None ⇒ giữ giá trị đang lưu) và
    các trang ghi lại `page_rewrites`."""

    layout_version: int = Field(ge=1)
    layout_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocks_after_close: bool | None = None
    pages: list[PageLayout] = Field(default_factory=list)
    page_rewrites: list[PageRewrite] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_pages(self) -> DocumentLayout:
        numbers = [page.page_number for page in self.pages]
        if len(set(numbers)) != len(numbers):
            raise ValueError("mỗi trang tối đa một PageLayout")
        rewrites = [rewrite.page.page_number for rewrite in self.page_rewrites]
        if len(set(rewrites)) != len(rewrites):
            raise ValueError("mỗi trang tối đa một PageRewrite")
        return self


class BlockFlagUpdate(BaseModel):
    """Thêm/gỡ cờ của MỘT khối (`update_block_flags`); một cờ không được vừa thêm vừa gỡ."""

    block_id: str = Field(pattern=r"^blk_[0-9a-f]{16}$")
    add: list[BlockFlag] = Field(default_factory=list)
    remove: list[BlockFlag] = Field(default_factory=list)

    @model_validator(mode="after")
    def _disjoint(self) -> BlockFlagUpdate:
        if set(self.add) & set(self.remove):
            raise ValueError("một cờ không được vừa thêm vừa gỡ")
        return self


class StructureProposalRecord(BaseModel):
    """Một hàng `structure_proposals` (§5.13; V03 ghi). `model` chỉ nằm trong DB cục bộ, không ra response hay log;
    `ops_json` là JSON thao tác của V03, kho lưu nguyên văn."""

    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    status: Literal["applied", "rejected"]
    ops_json: str
    created_at: datetime


CropWriteResult = Literal["stored", "capped"]
_CROP_FLAGS: frozenset[BlockFlag] = frozenset({"crop_pending", "crop_failed", "crop_capped"})
_SECTION_FLAG_ORDER: tuple[SectionFlag, ...] = get_args(SectionFlag)


def heading_levels(sections: Sequence[Section]) -> list[tuple[str, int]]:
    """`(block_id, level)` của mọi khối tiêu đề trong cây: khối tiêu đề của một Section (không phải gốc) nhận `level`
    của Section đó (`replace_sections` ghi vào `Block.level`). Một khối tiêu đề thuộc hai Section ⇒ `ValueError`."""
    levels: dict[str, int] = {}
    for section in sections:
        if section.kind == "chapter":
            continue
        for block_id in section.heading_block_ids:
            if block_id in levels:
                raise ValueError(f"khối tiêu đề {block_id} thuộc hai Section")
            levels[block_id] = section.level
    return sorted(levels.items())


def with_root_chapter(root: Section, chapter_code: str | None, chapter_title: str | None) -> Section:
    """Section gốc với `chapter_code` và `title` mới (NFC + gộp khoảng trắng, cắt 300 ký tự; rỗng/None ⇒ `title`
    None + cờ `untitled`); `section_id` và mọi field khác giữ nguyên."""
    title = normalize_chapter_title(chapter_title)[:MAX_CHAPTER_TITLE_CHARS].strip() if chapter_title else ""
    flags = set(root.flags) - {"untitled"} if title else set(root.flags) | {"untitled"}
    return Section.model_validate(
        {
            **root.model_dump(),
            "chapter_code": chapter_code,
            "title": title or None,
            "flags": [flag for flag in _SECTION_FLAG_ORDER if flag in flags],
        }
    )


def with_flags(block: Block, update: BlockFlagUpdate) -> Block:
    """Khối sau khi thêm/gỡ cờ theo `update`; vi phạm ràng buộc của `Block` ⇒ `ValueError` (pydantic)."""
    flags = (set(block.flags) | set(update.add)) - set(update.remove)
    return Block.model_validate({**block.model_dump(), "flags": sorted(flags)})


def capped(block: Block) -> Block:
    """Khối không lưu được ảnh cắt: giữ `crop_pending` (nếu có), thêm `crop_capped`."""
    return with_flags(block, BlockFlagUpdate(block_id=block.block_id, add=["crop_capped"]))


def cropped(block: Block, crop_ref: str) -> Block:
    """Khối đã có ảnh cắt `crop_ref`: gỡ `crop_pending`, `crop_failed`, `crop_capped`."""
    flags = [flag for flag in block.flags if flag not in _CROP_FLAGS]
    return Block.model_validate({**block.model_dump(), "crop_ref": crop_ref, "flags": flags})


def check_crop_bytes(crop: CropImage, png: bytes) -> None:
    """`crop_ref` là sha256 của `png` và `size_bytes` là độ dài của nó; sai ⇒ `ValueError`."""
    if hashlib.sha256(png).hexdigest() != crop.crop_ref:
        raise ValueError("crop_ref phải là sha256 của byte PNG")
    if len(png) != crop.size_bytes:
        raise ValueError("size_bytes phải bằng độ dài byte PNG")


class BlockRepository(Protocol):
    """Khối, mục, sổ bảo toàn, ảnh cắt, đề xuất cấu trúc và thế hệ nhập của tài liệu (§5.13, §5.15; ADR-010 mục 13,
    17, 19). Base của `DocumentRepository`: mọi hàm nhận `DocumentRepository` gọi thẳng các method này.

    Model qua ranh giới là model domain (`Block`, `ExtractionLedger`, `Section`, `CropImage`). Tài liệu không có ⇒
    `RecordNotFoundError` ở mọi method ghi.
    """

    def get_page_geometry(self, document_id: str, page_number: int) -> PageGeometry | None:
        """Hình học đã lưu của trang (cột `pages.geometry_json`); trang không có hoặc chưa có hình học ⇒ `None`."""
        ...

    def save_document_layout(self, document_id: str, layout: DocumentLayout) -> None:
        """Lưu NGUYÊN TỬ trong MỘT giao dịch: thay khối và ledger của mọi trang trong `layout.pages` (khối mới có
        `eligible = 0`), ghi `layout_version`/`layout_settings_sha256` (và `blocks_after_close` khi khác None) của
        tài liệu (KHÔNG tăng `version` của tài liệu), ghi lại các trang trong `layout.page_rewrites`, rồi xoá hàng
        ảnh cắt có `block_id` không còn trong khối của tài liệu.

        Một trang ghi lại lệch version ⇒ `VersionConflictError`; trang thiếu (khối hay ghi lại) ⇒
        `RecordNotFoundError`; cả hai trường hợp KHÔNG ghi gì. File ảnh không còn hàng nào trỏ tới bị xoá SAU commit.
        """
        ...

    def list_blocks(self, document_id: str, page_number: int | None = None) -> list[Block]:
        """Khối đã lưu của tài liệu (hoặc của một trang), sắp theo `(page_number, reading_order, block_id)`."""
        ...

    def list_extraction_ledgers(self, document_id: str) -> list[ExtractionLedger]:
        """Sổ bảo toàn đã lưu của tài liệu, sắp theo `page_number`."""
        ...

    def replace_sections(self, document_id: str, sections: Sequence[Section]) -> None:
        """Thay TOÀN BỘ Section của tài liệu (xoá rồi ghi) và, cùng giao dịch, ghi `Block.level` của khối `heading`
        = `level` của Section mà khối là tiêu đề (`heading_block_ids`; Section gốc không có tiêu đề), để `Block.level`
        và `Section.level` luôn khớp. Section khác tài liệu, id trùng hay khối tiêu đề không phải `heading` đã lưu
        ⇒ `ValueError`, không ghi gì."""
        ...

    def list_sections(self, document_id: str) -> list[Section]:
        """Section đã lưu, sắp theo `ordinal`."""
        ...

    def update_imported_document_and_root_section(
        self, document: ImportedDocument, expected_version: int
    ) -> ImportedDocument:
        """`update_imported_document` và, cùng giao dịch, cập nhật `title` (chuẩn hoá NFC + gộp khoảng trắng; None ⇒
        cờ `untitled`) và `chapter_code` của Section gốc (nếu có) theo `document`; `section_id` không đổi.

        Lệch version ⇒ `VersionConflictError`, không ghi gì.
        """
        ...

    def update_block_flags(self, document_id: str, updates: Sequence[BlockFlagUpdate]) -> list[Block]:
        """Thêm/gỡ cờ của các khối trong MỘT giao dịch; trả khối đã ghi theo thứ tự vào. Khối không có ⇒
        `RecordNotFoundError`; kết quả vi phạm ràng buộc của `Block` ⇒ `ValueError`; cả hai không ghi gì."""
        ...

    def save_structure_proposal(self, record: StructureProposalRecord) -> None:
        """Ghi (hoặc thay) đề xuất cấu trúc của trang; trang không có ⇒ `RecordNotFoundError`."""
        ...

    def list_structure_proposals(self, document_id: str) -> list[StructureProposalRecord]:
        """Đề xuất cấu trúc đã lưu của tài liệu, sắp theo `page_number`."""
        ...

    def refresh_eligible_blocks(self, document_id: str) -> int:
        """Hàm DUY NHẤT ghi `blocks.eligible`: trong MỘT giao dịch, đặt `eligible` của mọi khối theo
        `domain.block_eligibility.is_candidate_block` trên trạng thái trang HIỆN TẠI và `source_kind` của tài liệu.
        Trả số khối `eligible = 1`."""
        ...

    def list_eligible_blocks(self, document_id: str) -> list[Block]:
        """Khối có `eligible = 1`, sắp như `list_blocks`."""
        ...

    def save_crop_image(self, crop: CropImage, png: bytes) -> CropWriteResult:
        """Ghi ảnh cắt của VÙNG một khối: hàng `crop_images` (file PNG ghi TRƯỚC commit) và, cùng giao dịch, đặt
        `crop_ref` của khối, gỡ `crop_pending`/`crop_failed`/`crop_capped` ⇒ `"stored"`.

        Không có chỗ lưu (kho SQLite mở không có thư mục ảnh) hoặc vượt trần byte của kho in-memory ⇒ không ghi
        hàng/file, khối giữ `crop_pending` và thêm `crop_capped` ⇒ `"capped"`. `crop_ref` khác sha256 của `png`,
        `size_bytes` khác độ dài hay trang khác trang của khối ⇒ `ValueError`; khối không có ⇒ `RecordNotFoundError`.
        """
        ...

    def list_crop_images(self, document_id: str) -> list[CropImage]:
        """Hàng ảnh cắt của tài liệu, sắp theo `block_id`."""
        ...

    def get_crop_bytes(self, document_id: str, crop_ref: str) -> bytes | None:
        """Byte PNG khi tài liệu có ít nhất một hàng với `crop_ref` này và file còn; ngược lại `None`."""
        ...


class DocumentRepository(BlockRepository, Protocol):
    """Tài liệu nhập qua §6.5 và trang của nó. Trang lưu dạng JSON của model trang do tầng gọi chọn (§5.7); bộ nhập
    dùng `PageExtraction`, và `list_page_summaries` đọc các field tóm tắt của model đó."""

    def get_document(self, document_id: str) -> UploadedDocument | None:
        """Dòng `documents` bất kể ai tạo (upload đề §6.1 hoặc bộ nhập §6.5); như `IngestionRepository`."""
        ...

    def save_imported_document(self, document: ImportedDocument) -> ImportedDocument:
        """Tạo mới, hoặc bổ sung thông tin cho bản ghi đã có cùng `document_id` (version tăng 1)."""
        ...

    def create_imported_document(
        self, document: ImportedDocument, pending_pages: Sequence[BaseModel]
    ) -> ImportedDocument:
        """Tạo tài liệu nhập và MỌI trang của nó (trạng thái `pending`, chưa xác nhận) trong MỘT giao dịch.

        Phần tử thứ i của `pending_pages` là trang số i + 1; số phần tử phải bằng `meta.page_count`, sai ⇒
        `ValueError`. Bản ghi chỉ do upload đề tạo (chưa có `subject_code`) được bổ sung thông tin như
        `save_imported_document`. Đã có tài liệu nhập cùng `document_id` ⇒ `DocumentExistsError`. Lỗi ở bất kỳ bước
        nào (kể cả `StorageBusyError` giữa chừng) ⇒ không ghi gì. Trả bản ghi đã lưu.

        Môn của tài liệu chưa có thì được tạo ngầm trong CÙNG giao dịch (§5.14, `Subject.implicit(subject_code,
        meta.uploaded_at)`); môn đã có giữ nguyên. Lỗi ⇒ không có môn nào được tạo.

        Cùng giao dịch: bộ đếm thế hệ nhập của `document_id` tăng 1 (tạo = 1; bộ đếm sống qua xoá tài liệu) và
        `import_generation` của bản ghi trả về bằng giá trị đó. Bốn field khối của `document` (`layout_version`, …)
        bị bỏ qua: tài liệu mới chưa có khối.
        """
        ...

    def get_imported_document(self, document_id: str) -> ImportedDocument | None:
        """`None` khi không có bản ghi, hoặc bản ghi chỉ do upload đề tạo (chưa có `subject_code`)."""
        ...

    def update_imported_document(self, document: ImportedDocument, expected_version: int) -> ImportedDocument:
        """So-và-ghi nguyên tử; trả bản ghi với `version = expected_version + 1`."""
        ...

    def list_imported_documents(self, subject_code: str | None = None) -> list[ImportedDocument]:
        """Mới nhất trước (theo `uploaded_at`, hoà thì theo `document_id`)."""
        ...

    def delete_document(self, document_id: str) -> bool:
        """Xoá tài liệu cùng trang, chunk, chỉ mục, cặp khái niệm, khối, mục, ledger, đề xuất cấu trúc và ảnh cắt
        (hàng trong giao dịch, file/byte ảnh SAU commit); `False` khi không có. Bộ đếm thế hệ nhập KHÔNG bị xoá.

        Còn item tham chiếu ⇒ `RecordInUseError`.
        """
        ...

    def revert_imported_document(self, document_id: str, upload: UploadedDocument | None) -> bool:
        """Gỡ đúng phần mà một lần nhập §6.5 vừa thêm, trong MỘT giao dịch; `False` khi không có tài liệu nhập.

        `upload` là dòng `documents` do upload đề §6.1 tạo TRƯỚC lần nhập này (đọc bằng `get_document` trước khi
        gọi `create_imported_document`), hoặc `None` khi lần nhập tạo dòng mới:
        - `None` ⇒ xoá hẳn như `delete_document` (còn item tham chiếu ⇒ `RecordInUseError`, không xoá gì);
        - có giá trị ⇒ giữ dòng (item của §6.1 vẫn trỏ được tới), trả các cột về đúng `upload`, bỏ mọi thông tin
          của bộ nhập (môn, chương, tiêu đề, trạng thái, cột khối…) cùng trang, lịch sử sửa trang, chunk, khối, mục,
          ledger, đề xuất cấu trúc và ảnh cắt; version tăng 1. Bộ đếm thế hệ nhập không giảm.

        Trong cùng giao dịch, môn của tài liệu bị xoá nếu nó vẫn đúng là bản ghi mà `create_imported_document` của
        lần nhập này tạo ngầm (`Subject.implicit(subject_code, meta.uploaded_at)`, `version = 1`) và không còn tài
        liệu hay blueprint nào cùng `subject_code`: request lỗi thì không để lại môn tạo ngầm (§5.14).
        """
        ...

    def add_page(
        self, document_id: str, page_number: int, page: BaseModel, *, status: PageStatus, confirmed: bool
    ) -> None:
        """Thêm trang mới với version 1; tài liệu không có ⇒ `MissingReferenceError`."""
        ...

    def update_page(
        self,
        document_id: str,
        page_number: int,
        page: BaseModel,
        *,
        status: PageStatus,
        confirmed: bool,
        expected_version: int,
        audit: PageEditAudit | None = None,
        geometry: PageGeometry | None = None,
    ) -> int:
        """So-và-ghi nguyên tử; trả version mới (`expected_version + 1`).

        `audit` (lần sửa dòng, §6.5) được ghi trong cùng giao dịch với trang; nó phải thuộc đúng trang này và có
        `from_version = expected_version` (`check_page_audit`), sai ⇒ `ValueError`, không ghi gì. `geometry` khác None
        được ghi vào `pages.geometry_json` cùng giao dịch với JSON trang; None ⇒ giữ hình học đã lưu.
        """
        ...

    def list_page_audits(self, document_id: str, page_number: int) -> list[PageEditAudit]:
        """Lịch sử sửa dòng của trang theo thứ tự ghi; trang không có ⇒ danh sách rỗng."""
        ...

    def get_page[M: BaseModel](self, document_id: str, page_number: int, model: type[M]) -> M | None: ...

    def list_pages[M: BaseModel](self, document_id: str, model: type[M]) -> list[M]:
        """Theo `page_number` tăng dần."""
        ...

    def list_page_summaries(self, document_id: str) -> list[PageSummary]:
        """Tóm tắt từng trang (`DocumentImport.pages`), theo `page_number` tăng dần, KHÔNG dựng lại các dòng.

        Dùng cho các `GET` hỏi trạng thái (client hỏi 1–2 s một lần, §6.5): chỉ đọc cột `status`, `confirmed` và các
        field `tier_used`, `review_reasons`, `page_role`, `dropped`, `confirmed_by` của JSON trang, không validate
        danh sách dòng của mọi trang.
        """
        ...


class VaultRepository(Protocol):
    """Chunk của Knowledge Vault, chỉ mục FTS5 và cặp khái niệm (§5.8, §5.13). Xếp hạng BM25 không thuộc repository.

    Bất biến repository tự giữ trong mỗi giao dịch ghi, không phụ thuộc tầng gọi:
    - I3 của cặp khái niệm: `definition_quote` là chuỗi con (NFC + gộp khoảng trắng) của `raw_text` của chunk
      (`domain.vault.check_definition_quote`); sai ⇒ `QuoteMismatchError`, không ghi gì. Khi nạp, cặp vi phạm (ví dụ
      DB bị sửa tay) không bao giờ được trả ra.
    - `linked_chunk_ids` theo §12.1 (`domain.vault.link_practice_chunks`) được tính lại cho cả môn khi nhập theo lô và
      khi sửa nhãn. Đây là dữ liệu dẫn xuất nên không tăng `version` của chunk được tính lại.
    - `chapter_code` của cặp khái niệm chép từ chunk: sửa `chapter_code` của chunk thì cặp của nó đổi theo (version
      của cặp tăng 1).
    """

    @property
    def vault_search_available(self) -> bool:
        """`False` khi SQLite thiếu FTS5; app vẫn chạy, tầng gọi dùng BM25 tự cài."""
        ...

    def add_chunk(self, chunk: KnowledgeChunk, search_body: str) -> None:
        """Ghi một chunk, `chunk_topics` và hai chỉ mục FTS5 (nếu có) trong cùng giao dịch; giữ nguyên
        `linked_chunk_ids` của `chunk` (thao tác cấp thấp; nhập tài liệu dùng `add_chunks`).

        `search_body` là token chỉ mục chính (ADR-006 mục 5) nối bằng dấu cách; chỉ mục không dấu nhận chính chuỗi đó
        sau `domain.search_text.strip_diacritics`. Tài liệu không có ⇒ `MissingReferenceError`; trùng `chunk_id` ⇒
        `StorageError`.
        """
        ...

    def add_chunks(self, chunks: Sequence[KnowledgeChunk]) -> list[KnowledgeChunk]:
        """Nhập theo lô, nguyên tử: mọi chunk chưa có được ghi (kèm chỉ mục) trong MỘT giao dịch, rồi tính lại
        `linked_chunk_ids` của các môn liên quan; lỗi ở bất kỳ chunk nào thì không ghi gì.

        Chunk đã có (cùng `chunk_id`) giữ nguyên bản đang lưu. Trả bản đang lưu của từng chunk, đúng thứ tự vào.
        Tài liệu không có ⇒ `MissingReferenceError`.
        """
        ...

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None: ...

    def has_document_chunks(self, document_id: str) -> bool:
        """Vault đã có ít nhất một chunk cắt từ tài liệu `document_id` (bộ nhập §6.5 dùng để biết văn bản đã vào
        Vault hay chưa, kể cả khi tài liệu chưa kịp thành `confirmed`)."""
        ...

    def list_chunks(self, subject_code: str, chapter_code: str | None = None) -> list[KnowledgeChunk]:
        """Sắp theo `(doc_id, page_number, chunk_id)` (§6.6)."""
        ...

    def match_chunk_ids(self, subject_code: str, fts_query: str, index: SearchIndex = "primary") -> list[str]:
        """`chunk_id` khớp biểu thức FTS5 `fts_query` trong môn `subject_code`, sắp theo `chunk_id` (chưa xếp hạng).

        Thiếu FTS5 ⇒ `VaultSearchUnavailableError`.
        """
        ...

    def update_chunk_labels(
        self, chunk: KnowledgeChunk, expected_version: int, edit: VaultEdit | None = None
    ) -> KnowledgeChunk:
        """So-và-ghi nguyên tử phần nhãn của chunk (§6.6): `chapter_code`, `category`, `tags`, `topic_keys`,
        `label_status`, `mapping_confidence`; cùng giao dịch: `chapter_code` của cặp khái niệm thuộc chunk,
        `linked_chunk_ids` của cả môn và người sửa (`edit`).

        Văn bản (`raw_text`, `verbatim_quote`) và các field khác không được ghi. Trả chunk đã lưu với
        `version = expected_version + 1`. Lệch version ⇒ `VersionConflictError`; không có chunk ⇒
        `RecordNotFoundError`.
        """
        ...

    def set_chunk_exercise(self, chunk_id: str, is_exercise: bool | None, expected_version: int) -> KnowledgeChunk:
        """So-và-ghi nguyên tử nhãn `is_exercise` của chunk (LLM gắn trong job đề xuất cặp, §5.8; `None` khi LLM thiếu
        nhãn, trả null hay nhãn xung đột); trả chunk đã lưu với `version = expected_version + 1`.

        Chỉ ghi `is_exercise`; nhãn khác, `label_status` và văn bản giữ nguyên. Lệch version ⇒
        `VersionConflictError`; không có chunk ⇒ `RecordNotFoundError`.
        """
        ...

    def add_concept_pair(self, pair: ConceptPair, edit: VaultEdit | None = None) -> ConceptPair:
        """Ghi cặp mới. Chunk không có ⇒ `MissingReferenceError`; trích dẫn không khớp nguyên văn chunk ⇒
        `QuoteMismatchError` (I3, kiểm trong giao dịch ghi)."""
        ...

    def get_concept_pair(self, pair_id: str) -> ConceptPair | None: ...

    def list_concept_pairs(
        self, subject_code: str, chapter_code: str | None = None, status: ConceptPairStatus | None = None
    ) -> list[ConceptPair]:
        """Sắp theo `(chapter_code, term, pair_id)` (§6.6)."""
        ...

    def update_concept_pair(
        self, pair: ConceptPair, expected_version: int, edit: VaultEdit | None = None
    ) -> ConceptPair:
        """So-và-ghi nguyên tử theo version; trả cặp với `version = expected_version + 1`.

        `chunk_id`, `subject_code`, `chapter_code`, `proposed_by`, `created_at` không đổi. Lệch version ⇒
        `VersionConflictError`; không có cặp ⇒ `RecordNotFoundError`; trích dẫn không khớp nguyên văn chunk ⇒
        `QuoteMismatchError` (I3).
        """
        ...

    def get_last_edit(self, entity: VaultEntity, key: str) -> VaultEdit | None:
        """Người sửa và thời điểm của lần sửa gần nhất có `edit`; chưa sửa lần nào hoặc không có bản ghi ⇒ `None`."""
        ...


class AiCacheRepository(Protocol):
    """Cache AI gắn tài liệu (spec §5.16; ADR-009 mục 8). Chỉ lưu JSON đã validate; người đọc validate lại, hỏng thì
    gọi `drop_ai_cache_entry`. `delete_document` của store xoá liên kết của tài liệu và dòng không còn liên kết trong
    CÙNG giao dịch."""

    def get_ai_cache(self, cache_key: str, document_id: str, *, now: datetime) -> AiCacheEntry | None:
        """Trúng ⇒ cập nhật `last_used_at = now` và gắn dòng với `document_id`; dòng quá TTL coi như trượt."""
        ...

    def put_ai_cache(self, entry: AiCacheEntry, document_id: str, *, now: datetime) -> None:
        """Ghi (thay dòng cùng khoá) và gắn `document_id`. Cùng giao dịch: xoá dòng quá TTL, rồi xoá dòng
        `last_used_at` cũ nhất khi vượt trần số dòng."""
        ...

    def drop_ai_cache_entry(self, cache_key: str) -> None:
        """Xoá một dòng (kèm liên kết), ví dụ khi JSON đọc lại không còn hợp lệ."""
        ...

    def clear_ai_cache(self) -> int:
        """Xoá toàn bộ cache AI; trả số dòng `ai_cache` đã xoá (liên kết xoá theo)."""
        ...


class AppStore(
    IngestionRepository,
    BlueprintRepository,
    TwinBatchRepository,
    DocumentRepository,
    VaultRepository,
    SubjectRepository,
    PracticeAttemptRepository,
    AiCacheRepository,
    Protocol,
):
    """Kho mà các router dùng, cấp qua `Depends(get_store)`."""
