-- Nhãn bài tập của chunk và cổng tích sẵn của cặp khái niệm (SPECIFICATION §5.8, §5.13 bảng `chunks`,
-- `concept_pairs`; ADR-007 mục 3; GĐ1 1C, thêm 2026-10-31). Chỉ thêm cột, không đổi dữ liệu cũ.
-- - `chunks.is_exercise`: nhãn "chunk là bài tập" do LLM gắn trong job đề xuất cặp (0/1); NULL khi chưa có LLM gắn.
-- - `concept_pairs.pre_selected`: máy tích sẵn ô Chấp nhận (0/1), mặc định 0; cặp cũ không được tích.
-- - `concept_pairs.failed_gates_json`: danh sách `PairGate` trượt dạng JSON, mặc định `[]`.

ALTER TABLE chunks ADD COLUMN is_exercise INTEGER CHECK (is_exercise IS NULL OR is_exercise IN (0, 1));
ALTER TABLE concept_pairs ADD COLUMN pre_selected INTEGER NOT NULL DEFAULT 0 CHECK (pre_selected IN (0, 1));
ALTER TABLE concept_pairs ADD COLUMN failed_gates_json TEXT NOT NULL DEFAULT '[]';
