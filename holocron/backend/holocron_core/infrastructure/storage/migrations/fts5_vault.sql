-- Chỉ mục tìm kiếm của Knowledge Vault (SPECIFICATION §5.13, ADR-006 mục 5).
-- KHÔNG phải migration đánh số: kho chỉ chạy script này khi SQLite có ENABLE_FTS5 (đọc qua PRAGMA compile_options),
-- và chạy lại mỗi lần mở kho (IF NOT EXISTS). Thiếu FTS5 thì kho vẫn mở, chỉ báo `vault_search_available = false`.
--
-- FTS5 chỉ lọc ứng viên; điểm BM25 và việc so token do Python làm (`services/vault.py`). Để FTS5 không bao giờ loại
-- sót một chunk mà bộ tách từ Python (`domain/search_text.py`) coi là khớp, FTS5 không tự chuẩn hoá gì thêm:
-- - `body` của cả hai bảng là chính các token Python nối bằng một dấu cách, do repository ghi cùng giao dịch với
--   `chunks`: `chunks_fts` nhận token chỉ mục chính, `chunks_fts_nodiac` nhận token đó sau
--   `search_text.strip_diacritics` (bỏ MỌI dấu kết hợp, kể cả chữ Kirin, Hy Lạp). Vì vậy bảng không dấu cũng dùng
--   `remove_diacritics 0`: `remove_diacritics 2` của unicode61 chỉ bỏ dấu chữ Latin, nên `йод`/`иод` hay
--   `άλφα`/`αλφα` sẽ khớp ở Python mà không khớp ở FTS5.
-- - `tokenchars` thêm 21 ký tự Python coi là chữ (`str.isalnum`) nhưng bảng Unicode 6.1 của unicode61 coi là dấu
--   phân cách: U+19B0–U+19C0, U+19C8, U+19C9 (New Tai Lue), U+1CF2, U+1CF3 (Vedic). Thiếu chúng thì một token chỉ
--   gồm các ký tự này thành cụm từ rỗng và không khớp gì. `tests/test_vault_fts_parity.py` kiểm mọi điểm mã.

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
    body,
    chunk_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0 tokenchars ''ᦰᦱᦲᦳᦴᦵᦶᦷᦸᦹᦺᦻᦼᦽᦾᦿᧀᧈᧉᳲᳳ'''
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts_nodiac USING fts5 (
    body,
    chunk_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0 tokenchars ''ᦰᦱᦲᦳᦴᦵᦶᦷᦸᦹᦺᦻᦼᦽᦾᦿᧀᧈᧉᳲᳳ'''
);
