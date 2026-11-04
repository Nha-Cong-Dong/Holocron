-- Chỉ mục tra cứu khối không dấu (SPECIFICATION §5.13, ADR-010 mục 18).
-- KHÔNG phải migration đánh số: kho chỉ chạy script này khi SQLite có ENABLE_FTS5, và chạy lại mỗi lần mở kho
-- (IF NOT EXISTS). Thiếu FTS5 thì kho vẫn mở và tra cứu khối dùng đường lùi Python.
--
-- `body` là token đã gấp dấu bằng Python (`domain.vault.block_index_body`: NFC, casefold, bỏ dấu kết hợp, `đ` thành `d`)
-- nối bằng một dấu cách, do repository ghi cùng giao dịch với `blocks.eligible` (`refresh_eligible_blocks`). Vì vậy
-- bảng dùng `remove_diacritics 0` như `fts5_vault.sql`, cùng `tokenchars` (xem giải thích ở đó). Chỉ khối đủ điều
-- kiện có dòng ở đây; bảng ảo không có khoá ngoại nên xoá khối/tài liệu dọn bằng code.

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5 (
    body,
    block_id UNINDEXED,
    document_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 0 tokenchars ''ᦰᦱᦲᦳᦴᦵᦶᦷᦸᦹᦺᦻᦼᦽᦾᦿᧀᧈᧉᳲᳳ'''
);
