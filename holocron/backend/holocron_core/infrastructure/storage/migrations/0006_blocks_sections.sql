-- Migration khối: khối, mục, ảnh cắt, sổ bảo toàn, đề xuất cấu trúc (SPECIFICATION §5.13 "Khối, mục, ảnh cắt,
-- ledger"; ADR-010 mục 13, 17, 19). Chỉ thêm bảng và cột; dữ liệu cũ không đổi, tài liệu cũ không tự nhập lại (I4):
-- `layout_version`/`layout_settings_sha256` NULL, `blocks_after_close = 0`, `import_generation = 0` (nhập trước
-- migration này, không có hàng `import_generations`); trang cũ `geometry_json` NULL; chunk cũ `section_id` NULL,
-- `block_ids_json = '[]'`. Không có FTS khối ở đây (ADR-010 mục 18: script có điều kiện, X10).

ALTER TABLE documents ADD COLUMN layout_version INTEGER CHECK (layout_version IS NULL OR layout_version >= 1);
ALTER TABLE documents ADD COLUMN layout_settings_sha256 TEXT
    CHECK (layout_settings_sha256 IS NULL OR length(layout_settings_sha256) = 64);
ALTER TABLE documents ADD COLUMN blocks_after_close INTEGER NOT NULL DEFAULT 0 CHECK (blocks_after_close IN (0, 1));
ALTER TABLE documents ADD COLUMN import_generation INTEGER NOT NULL DEFAULT 0 CHECK (import_generation >= 0);

ALTER TABLE pages ADD COLUMN geometry_json TEXT;

-- Bộ đếm thế hệ nhập: KHÔNG khoá ngoại, KHÔNG xoá khi xoá tài liệu (thế hệ tăng đơn điệu qua xoá rồi nhập lại).
CREATE TABLE import_generations (
    document_id     TEXT PRIMARY KEY,
    last_generation INTEGER NOT NULL CHECK (last_generation >= 1)
);

-- `eligible` chỉ do `refresh_eligible_blocks` ghi (§5.15).
CREATE TABLE blocks (
    document_id   TEXT NOT NULL,
    block_id      TEXT NOT NULL,
    page_number   INTEGER NOT NULL CHECK (page_number >= 1),
    reading_order INTEGER NOT NULL CHECK (reading_order >= 0),
    type          TEXT NOT NULL,
    eligible      INTEGER NOT NULL DEFAULT 0 CHECK (eligible IN (0, 1)),
    block_json    TEXT NOT NULL,
    PRIMARY KEY (document_id, block_id),
    FOREIGN KEY (document_id, page_number) REFERENCES pages (document_id, page_number) ON DELETE CASCADE
);
CREATE INDEX ix_blocks_page_order ON blocks (document_id, page_number, reading_order);
CREATE INDEX ix_blocks_eligible ON blocks (document_id, eligible);

CREATE TABLE sections (
    document_id  TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    section_id   TEXT NOT NULL,
    parent_id    TEXT,
    ordinal      INTEGER NOT NULL CHECK (ordinal >= 0),
    section_json TEXT NOT NULL,
    PRIMARY KEY (document_id, section_id)
);
CREATE INDEX ix_sections_parent_ordinal ON sections (document_id, parent_id, ordinal);

-- Khoá theo VÙNG; `crop_ref` (sha256 PNG) là khoá FILE nội dung, nhiều hàng có thể chung một file. Không khoá ngoại
-- tới `blocks` (khối được ghi lại khi dựng lại; code xoá hàng của `block_id` không còn trong cùng giao dịch).
CREATE TABLE crop_images (
    document_id TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    block_id    TEXT NOT NULL,
    crop_ref    TEXT NOT NULL CHECK (length(crop_ref) = 64),
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    bbox_json   TEXT NOT NULL,
    width_px    INTEGER NOT NULL CHECK (width_px >= 1),
    height_px   INTEGER NOT NULL CHECK (height_px >= 1),
    size_bytes  INTEGER NOT NULL CHECK (size_bytes >= 1),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (document_id, block_id)
);
CREATE INDEX ix_crop_images_ref ON crop_images (document_id, crop_ref);

CREATE TABLE extraction_ledgers (
    document_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    balanced    INTEGER NOT NULL CHECK (balanced IN (0, 1)),
    ledger_json TEXT NOT NULL,
    PRIMARY KEY (document_id, page_number),
    FOREIGN KEY (document_id, page_number) REFERENCES pages (document_id, page_number) ON DELETE CASCADE
);
CREATE INDEX ix_extraction_ledgers_balanced ON extraction_ledgers (document_id, balanced);

-- `model` chỉ nằm trong DB cục bộ, không ra response hay log.
CREATE TABLE structure_proposals (
    document_id    TEXT NOT NULL,
    page_number    INTEGER NOT NULL,
    input_sha256   TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('applied', 'rejected')),
    ops_json       TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (document_id, page_number),
    FOREIGN KEY (document_id, page_number) REFERENCES pages (document_id, page_number) ON DELETE CASCADE
);

ALTER TABLE chunks ADD COLUMN section_id TEXT;
ALTER TABLE chunks ADD COLUMN block_ids_json TEXT NOT NULL DEFAULT '[]';
