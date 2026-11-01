/** Hỏi tiến độ job (T2.2): 2 s một lần, tối đa 600 lượt, dừng khi job kết thúc, huỷ hoặc lỗi liên tiếp. */

import { describe, expect, it } from "vitest";

import { type ImportResultView, parseImportResult } from "./import-result";
import {
  createJobPoller,
  JOB_POLL_INTERVAL_MS,
  JOB_POLL_MAX_CONSECUTIVE_ERRORS,
  JOB_POLL_MAX_POLLS,
  type JobPollEnd,
} from "./import-result-poll";
import type { Schedule } from "./poll";

function view(jobStatus: string | null): ImportResultView {
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
    proposal_job:
      jobStatus === null
        ? null
        : { job_id: "pjob_00112233445566ff", status: jobStatus, batches_total: 2, batches_done: 0 },
  });
  if (parsed === null) throw new Error("fixture phải parse được");
  return parsed;
}

/** Bộ hẹn giờ giả: ghi lại khoảng chờ, chạy tay từng lượt. */
function fakeSchedule(): { schedule: Schedule; delays: number[]; fire: () => boolean; pending: () => number } {
  const queue: { run: () => void; cancelled: boolean }[] = [];
  const delays: number[] = [];
  const schedule: Schedule = (run, delayMs) => {
    const entry = { run, cancelled: false };
    queue.push(entry);
    delays.push(delayMs);
    return () => {
      entry.cancelled = true;
    };
  };
  const pending = () => queue.filter((entry) => !entry.cancelled).length;
  const fire = () => {
    const entry = queue.shift();
    if (entry === undefined || entry.cancelled) return false;
    entry.run();
    return true;
  };
  return { schedule, delays, fire, pending };
}

async function flush(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe("createJobPoller", () => {
  it("hằng số theo spec: 2 s một lần, tối đa 600 lượt", () => {
    expect(JOB_POLL_INTERVAL_MS).toBe(2000);
    expect(JOB_POLL_MAX_POLLS).toBe(600);
  });

  it("hẹn lượt đầu sau 2 s, hỏi tiếp khi job còn chạy, dừng khi succeeded", async () => {
    const timer = fakeSchedule();
    const statuses = ["queued", "running", "succeeded"];
    const seen: (string | undefined)[] = [];
    const ends: JobPollEnd[] = [];
    let call = 0;
    const poller = createJobPoller({
      load: () => Promise.resolve(view(statuses[call++] ?? "succeeded")),
      onValue: (value) => seen.push(value.job?.status),
      onEnd: (reason) => ends.push(reason),
      schedule: timer.schedule,
    });
    poller.start();
    expect(call).toBe(0);
    expect(timer.delays).toEqual([2000]);
    for (let i = 0; i < 3; i += 1) {
      timer.fire();
      await flush();
    }
    expect(seen).toEqual(["queued", "running", "succeeded"]);
    expect(ends).toEqual(["finished"]);
    expect(poller.isRunning()).toBe(false);
    expect(timer.pending()).toBe(0);
  });

  it("dừng ở mọi trạng thái kết thúc và khi job biến mất", async () => {
    for (const status of ["succeeded", "partial", "failed", "cancelled", null]) {
      const timer = fakeSchedule();
      const ends: JobPollEnd[] = [];
      const poller = createJobPoller({
        load: () => Promise.resolve(view(status)),
        onValue: () => undefined,
        onEnd: (reason) => ends.push(reason),
        schedule: timer.schedule,
      });
      poller.start();
      timer.fire();
      await flush();
      expect(ends).toEqual(["finished"]);
      expect(timer.pending()).toBe(0);
    }
  });

  it("stop() huỷ hẹn giờ và bỏ phản hồi đang chờ (rời màn)", async () => {
    const timer = fakeSchedule();
    const seen: unknown[] = [];
    const ends: JobPollEnd[] = [];
    let release: (value: ImportResultView) => void = () => undefined;
    const poller = createJobPoller({
      load: () =>
        new Promise<ImportResultView>((resolve) => {
          release = resolve;
        }),
      onValue: (value) => seen.push(value),
      onEnd: (reason) => ends.push(reason),
      schedule: timer.schedule,
    });
    poller.start();
    timer.fire();
    poller.stop();
    release(view("running"));
    await flush();
    expect(seen).toEqual([]);
    expect(ends).toEqual([]);
    expect(timer.pending()).toBe(0);
    expect(poller.isRunning()).toBe(false);
  });

  it("stop() trước khi lượt đầu chạy huỷ hẹn giờ", () => {
    const timer = fakeSchedule();
    const poller = createJobPoller({
      load: () => Promise.resolve(view("running")),
      onValue: () => undefined,
      onEnd: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    poller.stop();
    expect(timer.pending()).toBe(0);
  });

  it("dừng và báo `limit` khi đủ trần số lượt", async () => {
    const timer = fakeSchedule();
    const ends: JobPollEnd[] = [];
    let loads = 0;
    const poller = createJobPoller({
      load: () => {
        loads += 1;
        return Promise.resolve(view("running"));
      },
      onValue: () => undefined,
      onEnd: (reason) => ends.push(reason),
      schedule: timer.schedule,
      maxPolls: 3,
    });
    poller.start();
    for (let i = 0; i < 6; i += 1) {
      timer.fire();
      await flush();
    }
    expect(loads).toBe(3);
    expect(ends).toEqual(["limit"]);
    expect(poller.isRunning()).toBe(false);
  });

  it("lỗi liên tiếp thì dừng và báo `errors`; một lượt thành công đặt lại bộ đếm", async () => {
    const timer = fakeSchedule();
    const ends: JobPollEnd[] = [];
    const results: ("err" | "ok")[] = ["err", "err", "ok", "err", "err", "err", "err"];
    let call = 0;
    const poller = createJobPoller({
      load: () => {
        const next = results[call++];
        return next === "ok" ? Promise.resolve(view("running")) : Promise.reject(new Error("mạng"));
      },
      onValue: () => undefined,
      onEnd: (reason) => ends.push(reason),
      schedule: timer.schedule,
    });
    poller.start();
    for (let i = 0; i < 12; i += 1) {
      timer.fire();
      await flush();
    }
    expect(JOB_POLL_MAX_CONSECUTIVE_ERRORS).toBe(4);
    expect(call).toBe(7);
    expect(ends).toEqual(["errors"]);
  });

  it("không có hai lượt chồng nhau: lượt kế chỉ hẹn sau khi lượt trước trả về", async () => {
    const timer = fakeSchedule();
    let release: (value: ImportResultView) => void = () => undefined;
    const poller = createJobPoller({
      load: () =>
        new Promise<ImportResultView>((resolve) => {
          release = resolve;
        }),
      onValue: () => undefined,
      onEnd: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    timer.fire();
    expect(timer.pending()).toBe(0);
    release(view("running"));
    await flush();
    expect(timer.pending()).toBe(1);
  });

  it("start() khi đang chạy không tạo thêm hẹn giờ", () => {
    const timer = fakeSchedule();
    const poller = createJobPoller({
      load: () => Promise.resolve(view("running")),
      onValue: () => undefined,
      onEnd: () => undefined,
      schedule: timer.schedule,
    });
    poller.start();
    poller.start();
    expect(timer.pending()).toBe(1);
  });
});
