-- Chương của tài liệu nhập qua bộ nhập PDF (SPECIFICATION §5.13, §6.5; duyệt 2026-10-26: một file = một chương).
-- NULL với dòng chỉ do upload đề §6.1 tạo, và với tài liệu nhập trước migration này (khi đó chưa có field form).
-- Chunk cắt khi xác nhận tài liệu chép giá trị này (§5.8 `KnowledgeChunk.chapter_code`).

ALTER TABLE documents ADD COLUMN chapter_code TEXT;
