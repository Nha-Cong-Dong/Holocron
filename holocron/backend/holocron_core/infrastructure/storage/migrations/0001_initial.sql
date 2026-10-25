-- Lược đồ đầu tiên của Holocron (SPECIFICATION §5.13).
-- Cột `*_json` chứa model_dump_json() của model Pydantic tương ứng và được validate lại khi đọc;
-- cột tách riêng chỉ để lọc và làm khoá. Thời gian lưu dạng ISO-8601 có múi giờ.
-- Bảng chỉ mục FTS5 (chunks_fts, chunks_fts_nodiac) KHÔNG nằm ở đây mà ở `fts5_vault.sql`,
-- vì SQLite thiếu FTS5 vẫn phải mở được kho (§5.13, "Kiểm FTS5").

CREATE TABLE documents (
    document_id     TEXT PRIMARY KEY,
    sha256          TEXT NOT NULL UNIQUE,
    filename        TEXT NOT NULL,
    media_type      TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL CHECK (size_bytes >= 1),
    subject_code    TEXT,
    title           TEXT,
    source_kind     TEXT,
    output_language TEXT,
    page_count      INTEGER CHECK (page_count IS NULL OR page_count >= 1),
    has_text_layer  INTEGER CHECK (has_text_layer IS NULL OR has_text_layer IN (0, 1)),
    status          TEXT,
    uploaded_at     TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_documents_subject_uploaded ON documents (subject_code, uploaded_at);

CREATE TABLE pages (
    document_id     TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    page_number     INTEGER NOT NULL CHECK (page_number >= 1),
    status          TEXT NOT NULL,
    extraction_json TEXT NOT NULL,
    confirmed       INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
    updated_at      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (document_id, page_number)
);
CREATE INDEX ix_pages_document_status ON pages (document_id, status);

CREATE TABLE page_audits (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL,
    page_number  INTEGER NOT NULL,
    from_version INTEGER NOT NULL,
    to_version   INTEGER NOT NULL,
    editor_id    TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    at           TEXT NOT NULL,
    FOREIGN KEY (document_id, page_number) REFERENCES pages (document_id, page_number) ON DELETE CASCADE
);
CREATE INDEX ix_page_audits_page ON page_audits (document_id, page_number, audit_id);

CREATE TABLE chunks (
    chunk_id              TEXT PRIMARY KEY,
    document_id           TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    subject_code          TEXT NOT NULL,
    chapter_code          TEXT NOT NULL,
    category              TEXT NOT NULL,
    page_number           INTEGER NOT NULL CHECK (page_number >= 1),
    page_end              INTEGER,
    raw_text              TEXT NOT NULL,
    verbatim_quote        TEXT NOT NULL,
    tags_json             TEXT NOT NULL,
    linked_chunk_ids_json TEXT NOT NULL,
    token_count           INTEGER NOT NULL CHECK (token_count >= 1),
    label_status          TEXT NOT NULL DEFAULT 'proposed' CHECK (label_status IN ('proposed', 'confirmed')),
    mapping_confidence    REAL CHECK (mapping_confidence IS NULL OR (mapping_confidence >= 0 AND mapping_confidence <= 1)),
    version               INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_chunks_subject_chapter ON chunks (subject_code, chapter_code);
CREATE INDEX ix_chunks_document_page ON chunks (document_id, page_number);

CREATE TABLE chunk_topics (
    chunk_id  TEXT NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    topic_key TEXT NOT NULL,
    PRIMARY KEY (chunk_id, topic_key)
);
CREATE INDEX ix_chunk_topics_topic ON chunk_topics (topic_key);

CREATE TABLE concept_pairs (
    pair_id            TEXT PRIMARY KEY,
    chunk_id           TEXT NOT NULL REFERENCES chunks (chunk_id) ON DELETE CASCADE,
    subject_code       TEXT NOT NULL,
    chapter_code       TEXT NOT NULL,
    term               TEXT NOT NULL,
    definition_quote   TEXT NOT NULL,
    term_spans_json    TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    proposed_by        TEXT NOT NULL CHECK (proposed_by IN ('llm', 'user')),
    mapping_confidence REAL CHECK (mapping_confidence IS NULL OR (mapping_confidence >= 0 AND mapping_confidence <= 1)),
    created_at         TEXT NOT NULL,
    confirmed_at       TEXT,
    version            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_concept_pairs_subject_status_chapter ON concept_pairs (subject_code, status, chapter_code);

CREATE TABLE ingestion_items (
    item_id        TEXT PRIMARY KEY,
    source_doc_id  TEXT NOT NULL REFERENCES documents (document_id) ON DELETE RESTRICT,
    page_number    INTEGER NOT NULL CHECK (page_number >= 1),
    topic_key      TEXT NOT NULL,
    approved       INTEGER NOT NULL CHECK (approved IN (0, 1)),
    non_cloneable  INTEGER NOT NULL CHECK (non_cloneable IN (0, 1)),
    item_json      TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_ingestion_items_doc_page ON ingestion_items (source_doc_id, page_number);

CREATE TABLE patch_audits (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      TEXT NOT NULL REFERENCES ingestion_items (item_id) ON DELETE CASCADE,
    table_id     TEXT NOT NULL,
    from_version INTEGER NOT NULL,
    to_version   INTEGER NOT NULL,
    editor_id    TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    at           TEXT NOT NULL
);
CREATE INDEX ix_patch_audits_item ON patch_audits (item_id, audit_id);

CREATE TABLE blueprints (
    blueprint_id   TEXT PRIMARY KEY,
    subject_code   TEXT NOT NULL,
    blueprint_json TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_blueprints_subject_created ON blueprints (subject_code, created_at);

CREATE TABLE blueprint_items (
    blueprint_id TEXT NOT NULL REFERENCES blueprints (blueprint_id) ON DELETE CASCADE,
    slot_index   INTEGER NOT NULL CHECK (slot_index >= 1),
    item_id      TEXT NOT NULL REFERENCES ingestion_items (item_id) ON DELETE RESTRICT,
    PRIMARY KEY (blueprint_id, slot_index)
);
CREATE INDEX ix_blueprint_items_item ON blueprint_items (item_id);

CREATE TABLE twin_batches (
    batch_id        TEXT PRIMARY KEY,
    blueprint_id    TEXT NOT NULL REFERENCES blueprints (blueprint_id) ON DELETE CASCADE,
    base_seed       INTEGER NOT NULL CHECK (base_seed >= 0),
    request_json    TEXT,
    body_digest     TEXT NOT NULL,
    idempotency_key TEXT,
    created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_twin_batches_idempotency ON twin_batches (blueprint_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_twin_batches_blueprint_created ON twin_batches (blueprint_id, created_at);

CREATE TABLE twins (
    twin_id    TEXT PRIMARY KEY,
    batch_id   TEXT NOT NULL REFERENCES twin_batches (batch_id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL,
    ordinal    INTEGER NOT NULL,
    twin_json  TEXT NOT NULL
);
CREATE INDEX ix_twins_batch_slot_ordinal ON twins (batch_id, slot_index, ordinal);

CREATE TABLE review_tickets (
    ticket_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL REFERENCES twin_batches (batch_id) ON DELETE CASCADE,
    slot_index  INTEGER NOT NULL,
    ticket_json TEXT NOT NULL
);
CREATE INDEX ix_review_tickets_batch_slot ON review_tickets (batch_id, slot_index);

CREATE TABLE templates (
    template_id       TEXT PRIMARY KEY,
    topic_key         TEXT NOT NULL,
    tier              TEXT NOT NULL CHECK (tier IN ('candidate', 'verified')),
    provenance        TEXT NOT NULL,
    source_sha256     TEXT NOT NULL UNIQUE,
    generator_source  TEXT NOT NULL,
    metadata_json     TEXT NOT NULL,
    verification_json TEXT,
    approved_by       TEXT,
    approved_at       TEXT,
    created_at        TEXT NOT NULL,
    version           INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_templates_topic_tier ON templates (topic_key, tier);
