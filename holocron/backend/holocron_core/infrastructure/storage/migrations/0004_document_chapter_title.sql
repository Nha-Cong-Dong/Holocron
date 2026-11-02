-- Chương tự động và cài đặt tự xác nhận của tài liệu nhập (SPECIFICATION §5.7 "Chương tự động", §5.13 bảng
-- `documents`, §6.5; GĐ1 1A, 1B, thêm 2026-10-31). Chỉ thêm cột, không đổi dữ liệu cũ ngoài nguồn của `chapter_code`.
-- - `chapter_title`: tên chương; NULL với bản ghi tạo trước migration này và với dòng chỉ do upload đề §6.1 tạo.
-- - `chapter_code_source`: nguồn của `chapter_code`; bản ghi cũ có `chapter_code` (field form bắt buộc trước
--   2026-10-31) nhận `user`, còn lại NULL.
-- - `chapter_title_source`: nguồn của `chapter_title`; NULL đúng khi `chapter_title` NULL.
-- - `chapter_flags_json`: danh sách `ChapterFlag` dạng JSON, mặc định `[]`.
-- - `auto_confirm`: field form của `POST /v1/documents` (0/1), mặc định bật.

ALTER TABLE documents ADD COLUMN chapter_title TEXT
    CHECK (chapter_title IS NULL OR length(chapter_title) BETWEEN 1 AND 300);
ALTER TABLE documents ADD COLUMN chapter_code_source TEXT
    CHECK (chapter_code_source IS NULL OR chapter_code_source IN ('user', 'file_name', 'next_free'));
ALTER TABLE documents ADD COLUMN chapter_title_source TEXT
    CHECK (chapter_title_source IS NULL OR chapter_title_source IN ('user', 'repeated_header', 'file_name', 'llm'));
ALTER TABLE documents ADD COLUMN chapter_flags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE documents ADD COLUMN auto_confirm INTEGER NOT NULL DEFAULT 1 CHECK (auto_confirm IN (0, 1));

UPDATE documents SET chapter_code_source = 'user' WHERE chapter_code IS NOT NULL;
