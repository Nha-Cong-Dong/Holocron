-- Lượt làm bài và câu sai để ôn (SPECIFICATION §5.13 bảng `practice_attempts`, `practice_answers`; §5.17; §6.12).
-- Lượt không sửa được nên không có cột `version`; không lưu số đếm hay kết quả của lượt (code đếm lúc đọc).
-- - `practice_attempts.seq`: thứ tự ghi tăng đơn điệu (= MAX(seq) + 1 trong giao dịch ghi), dùng cho "gần nhất".
-- - Xoá batch (cascade `blueprints` → `twin_batches`) xoá lượt `batch` của batch đó và mọi hàng câu trỏ tới twin của
--   batch (cascade `twins` → `practice_answers`); trigger `tr_practice_answers_drop_empty_attempt` xoá lượt không còn
--   câu nào trong cùng giao dịch.

CREATE TABLE practice_attempts (
    attempt_id   TEXT PRIMARY KEY,
    seq          INTEGER NOT NULL UNIQUE,
    subject_code TEXT NOT NULL,
    mode         TEXT NOT NULL CHECK (mode IN ('batch', 'review')),
    batch_id     TEXT REFERENCES twin_batches (batch_id) ON DELETE CASCADE,
    blueprint_id TEXT,
    submitted_at TEXT NOT NULL,
    CHECK ((mode = 'review') = (batch_id IS NULL)),
    CHECK ((mode = 'review') = (blueprint_id IS NULL))
);
CREATE INDEX ix_practice_attempts_subject_seq ON practice_attempts (subject_code, seq);

CREATE TABLE practice_answers (
    attempt_id  TEXT NOT NULL REFERENCES practice_attempts (attempt_id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL CHECK (ordinal >= 0),
    twin_id     TEXT NOT NULL REFERENCES twins (twin_id) ON DELETE CASCADE,
    batch_id    TEXT NOT NULL,
    slot_index  INTEGER NOT NULL,
    chosen_key  TEXT CHECK (chosen_key IS NULL OR chosen_key IN ('A', 'B', 'C', 'D')),
    correct_key TEXT NOT NULL CHECK (correct_key IN ('A', 'B', 'C', 'D')),
    outcome     TEXT NOT NULL CHECK (outcome IN ('correct', 'incorrect', 'skipped')),
    PRIMARY KEY (attempt_id, ordinal),
    UNIQUE (attempt_id, twin_id)
);
CREATE INDEX ix_practice_answers_twin ON practice_answers (twin_id);

CREATE TRIGGER tr_practice_answers_drop_empty_attempt
AFTER DELETE ON practice_answers
WHEN NOT EXISTS (SELECT 1 FROM practice_answers WHERE attempt_id = OLD.attempt_id)
BEGIN
    DELETE FROM practice_attempts WHERE attempt_id = OLD.attempt_id;
END;
