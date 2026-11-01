/**
 * Hồi quy: theo dõi job dừng vì lỗi liên tiếp, người dùng bấm "Tải lại" ⇒ màn dựng lại một bộ hỏi mới
 * (ImportResult.tsx: `load()` đưa màn về "đang tải" rồi "ready", effect theo dõi chạy lại) và bộ mới phải hỏi lại.
 */

import { describe, expect, it } from "vitest";

import { type ImportResultView, parseImportResult } from "./import-result";
import { createJobPoller, JOB_POLL_MAX_CONSECUTIVE_ERRORS, type JobPollEnd } from "./import-result-poll";
import type { Schedule } from "./poll";

function runningView(): ImportResultView {
  const parsed = parseImportResult({
    meta: {
      doc_id: "doc_0123456789abcdef",
      subject_code: "MAU101",
      title: "t",
      source_kind: "textbook",
      page_count: 1,
      has_text_layer: true,
      uploaded_at: "2026-11-01T00:00:00Z",
    },
    filename: "a.pdf",
    size_bytes: 1,
    chapter_code: "ch1",
    status: "confirmed",
    pages: [],
    proposal_job: { job_id: "pjob_00112233445566ff", status: "running", batches_total: 2, batches_done: 0 },
  });
  if (parsed === null) throw new Error("fixture phải parse được");
  return parsed;
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe("theo dõi job sau khi dừng vì lỗi", () => {
  it("bộ hỏi mới (sau Tải lại) hỏi lại được dù bộ cũ đã dừng vì lỗi", async () => {
    const queue: (() => void)[] = [];
    const schedule: Schedule = (run) => {
      queue.push(run);
      return () => undefined;
    };
    const fire = async (): Promise<void> => {
      queue.shift()?.();
      await flush();
    };

    let failing = true;
    let calls = 0;
    const ends: JobPollEnd[] = [];
    const values: ImportResultView[] = [];
    const make = () =>
      createJobPoller({
        load: async () => {
          calls += 1;
          if (failing) throw new Error("mạng lỗi");
          return runningView();
        },
        onValue: (value) => values.push(value),
        onEnd: (reason) => ends.push(reason),
        schedule,
      });

    const first = make();
    first.start();
    for (let i = 0; i < JOB_POLL_MAX_CONSECUTIVE_ERRORS; i += 1) await fire();
    expect(ends).toEqual(["errors"]);
    expect(first.isRunning()).toBe(false);
    expect(calls).toBe(JOB_POLL_MAX_CONSECUTIVE_ERRORS);

    // "Tải lại": mạng đã ổn, màn dựng bộ hỏi mới.
    failing = false;
    const second = make();
    second.start();
    await fire();
    expect(calls).toBe(JOB_POLL_MAX_CONSECUTIVE_ERRORS + 1);
    expect(values).toHaveLength(1);
    expect(second.isRunning()).toBe(true);
    second.stop();
  });
});
