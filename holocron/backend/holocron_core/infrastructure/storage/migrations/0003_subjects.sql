-- Môn học và hình thức thi của môn (SPECIFICATION §5.13 bảng `subjects`, §5.14 `Subject`, §6.8).
-- Các bảng khác giữ `subject_code` dạng chuỗi, KHÔNG có khoá ngoại tới bảng này (§5.13), để nhập tài liệu không phụ
-- thuộc thứ tự tạo môn. Thời gian lưu dạng ISO-8601 UTC, micro giây, như `sqlite_store._ts`.

CREATE TABLE subjects (
    subject_code TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    exam_format  TEXT NOT NULL CHECK (exam_format IN ('mcq', 'essay', 'mixed')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
);

-- Dữ liệu tạo trước migration này: mỗi `subject_code` đã có ở một bảng thành một môn tạo ngầm
-- (`display_name = subject_code`, `exam_format = mixed`, §5.14), để `GET /v1/subjects` liệt kê được mà không phải ghi.
INSERT INTO subjects (subject_code, display_name, exam_format, created_at, updated_at)
SELECT DISTINCT code, code, 'mixed',
       strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'), strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now')
FROM (
    SELECT subject_code AS code FROM documents WHERE subject_code IS NOT NULL
    UNION ALL SELECT subject_code FROM chunks
    UNION ALL SELECT subject_code FROM concept_pairs
    UNION ALL SELECT subject_code FROM blueprints
);
