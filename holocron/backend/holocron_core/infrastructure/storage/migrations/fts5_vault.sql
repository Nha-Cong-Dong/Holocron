-- Chỉ mục tìm kiếm của Knowledge Vault (SPECIFICATION §5.13, ADR-006 mục 5).
-- KHÔNG phải migration đánh số: kho chỉ chạy script này khi SQLite có ENABLE_FTS5 (đọc qua PRAGMA compile_options),
-- và chạy lại mỗi lần mở kho (IF NOT EXISTS). Thiếu FTS5 thì kho vẫn mở, chỉ báo `vault_search_available = false`.
-- `body` là văn bản đã qua bước 1–3 của ADR-006 mục 5 do tầng gọi cấp; repository ghi cùng giao dịch với `chunks`.

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    body,
    chunk_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0'
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_nodiac USING fts5 (
    body,
    chunk_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
