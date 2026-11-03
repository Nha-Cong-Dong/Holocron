-- Cache AI dùng chung cho bộ đọc vùng VLM (V02) và đề xuất cấu trúc (V03) (SPECIFICATION §5.16; ADR-009 mục 8).
-- Khoá `cache_key` = sha256 hex của JSON chuẩn hoá [kind, input_sha256, model, prompt_version]. Chỉ lưu kết quả
-- `status = ok` đã validate; `model` chỉ nằm ở DB cục bộ. Một dòng thuộc được nhiều tài liệu qua `ai_cache_documents`;
-- xoá tài liệu xoá liên kết của nó rồi xoá dòng không còn liên kết, trong cùng giao dịch (B6').
-- Số migration: số kế tiếp lúc merge (ADR-010 mục 17); nhánh merge sau đổi số nếu trùng.

CREATE TABLE ai_cache (
    cache_key      TEXT PRIMARY KEY CHECK (length(cache_key) = 64),
    kind           TEXT NOT NULL CHECK (kind IN ('vlm_region', 'structure_proposal')),
    input_sha256   TEXT NOT NULL CHECK (length(input_sha256) = 64),
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    last_used_at   TEXT NOT NULL
);

CREATE INDEX ai_cache_last_used ON ai_cache (last_used_at);

CREATE TABLE ai_cache_documents (
    cache_key   TEXT NOT NULL REFERENCES ai_cache (cache_key) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    PRIMARY KEY (cache_key, document_id)
);

CREATE INDEX ai_cache_documents_by_document ON ai_cache_documents (document_id);
