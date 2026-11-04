-- Chỉ mục tra cứu mờ của khối (SPECIFICATION §5.13, ADR-010 mục 18); đi cùng `fts5_blocks.sql`.
-- Kho chỉ chạy script này khi probe trigram đạt (`migrator.trigram_available`: SQLite >= 3.34.0 và tạo thử bảng ảo
-- trigram trong `temp` thành công), và chạy lại mỗi lần mở kho (IF NOT EXISTS). `body` là văn bản đã gấp dấu
-- (`domain.vault.block_trigram_body`), gộp khoảng trắng; trigram không phân biệt hoa thường theo mặc định.

CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts_trigram USING fts5 (
    body,
    block_id UNINDEXED,
    document_id UNINDEXED,
    subject_code UNINDEXED,
    tokenize = 'trigram'
);
