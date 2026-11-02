import { describe, expect, it } from "vitest";

import {
  EMPTY_TOAST_QUEUE,
  MAX_VISIBLE_TOASTS,
  TOAST_DURATION_MS,
  type ToastQueue,
  toastDeadline,
  toastReducer,
} from "./toast-queue";

function pushAll(messages: readonly string[], from: ToastQueue = EMPTY_TOAST_QUEUE, now = 1000): ToastQueue {
  return messages.reduce((state, message) => toastReducer(state, { type: "push", message, now }), from);
}

describe("toastReducer: thêm, rời đi, gỡ", () => {
  it("thêm thông báo với id tăng dần, bắt đầu đếm từ lúc thêm", () => {
    const state = pushAll(["Đã nhận 6 khái niệm", "Đã đọc xong 3 file"]);
    expect(state.items).toEqual([
      { id: 1, message: "Đã nhận 6 khái niệm", leaving: false, remainingMs: TOAST_DURATION_MS, startedAt: 1000 },
      { id: 2, message: "Đã đọc xong 3 file", leaving: false, remainingMs: TOAST_DURATION_MS, startedAt: 1000 },
    ]);
    expect(state.nextId).toBe(3);
  });

  it("bỏ qua câu rỗng và cắt khoảng trắng hai đầu", () => {
    expect(pushAll(["   "])).toBe(EMPTY_TOAST_QUEUE);
    expect(pushAll(["  Đã lưu  "]).items[0]?.message).toBe("Đã lưu");
  });

  it("không chồng hai bản của cùng một câu đang hiện", () => {
    const once = pushAll(["Đã lưu"]);
    expect(toastReducer(once, { type: "push", message: "Đã lưu", now: 2000 })).toBe(once);
  });

  it("câu giống một thông báo đang rời đi thì vẫn được thêm", () => {
    const leaving = toastReducer(pushAll(["Đã lưu"]), { type: "leave", id: 1 });
    expect(toastReducer(leaving, { type: "push", message: "Đã lưu", now: 2000 }).items).toHaveLength(2);
  });

  it("quá số thông báo hiện cùng lúc thì cái cũ nhất bắt đầu rời đi", () => {
    const messages = Array.from({ length: MAX_VISIBLE_TOASTS + 1 }, (_, index) => `Thông báo ${index + 1}`);
    const state = pushAll(messages);
    expect(state.items.filter((item) => !item.leaving)).toHaveLength(MAX_VISIBLE_TOASTS);
    expect(state.items[0]?.leaving).toBe(true);
  });

  it("leave đánh dấu đúng thông báo; id lạ hoặc đã rời thì giữ nguyên state", () => {
    const state = pushAll(["A", "B"]);
    const left = toastReducer(state, { type: "leave", id: 2 });
    expect(left.items.map((item) => item.leaving)).toEqual([false, true]);
    expect(toastReducer(left, { type: "leave", id: 2 })).toBe(left);
    expect(toastReducer(left, { type: "leave", id: 99 })).toBe(left);
  });

  it("remove gỡ hẳn thông báo; id lạ thì giữ nguyên state", () => {
    const state = pushAll(["A", "B"]);
    const removed = toastReducer(state, { type: "remove", id: 1 });
    expect(removed.items.map((item) => item.message)).toEqual(["B"]);
    expect(toastReducer(removed, { type: "remove", id: 1 })).toBe(removed);
    expect(removed.nextId).toBe(3);
  });
});

describe("toastReducer: tạm dừng khi rê chuột hoặc focus", () => {
  it("hạn tự rời đi là lúc bắt đầu cộng thời gian còn lại", () => {
    const item = pushAll(["A"]).items[0];
    expect(item === undefined ? null : toastDeadline(item)).toBe(1000 + TOAST_DURATION_MS);
  });

  it("tạm dừng giữ phần thời gian còn lại và bỏ hạn", () => {
    const paused = toastReducer(pushAll(["A"]), { type: "pause", now: 3000 });
    expect(paused.paused).toBe(true);
    expect(paused.items[0]).toMatchObject({ remainingMs: TOAST_DURATION_MS - 2000, startedAt: null });
    const item = paused.items[0];
    expect(item === undefined ? undefined : toastDeadline(item)).toBeNull();
  });

  it("đếm tiếp từ lúc rời ra với đúng phần còn lại", () => {
    let state = toastReducer(pushAll(["A"]), { type: "pause", now: 3000 });
    state = toastReducer(state, { type: "resume", now: 10_000 });
    const item = state.items[0];
    expect(state.paused).toBe(false);
    expect(item === undefined ? null : toastDeadline(item)).toBe(10_000 + TOAST_DURATION_MS - 2000);
  });

  it("thời gian còn lại không âm khi tạm dừng sau hạn", () => {
    const paused = toastReducer(pushAll(["A"]), { type: "pause", now: 1000 + TOAST_DURATION_MS + 500 });
    expect(paused.items[0]?.remainingMs).toBe(0);
  });

  it("thông báo thêm lúc đang tạm dừng chưa đếm giờ cho tới khi rời ra", () => {
    const paused = toastReducer(EMPTY_TOAST_QUEUE, { type: "pause", now: 0 });
    const added = toastReducer(paused, { type: "push", message: "A", now: 500 });
    expect(added.items[0]).toMatchObject({ remainingMs: TOAST_DURATION_MS, startedAt: null });
    const resumed = toastReducer(added, { type: "resume", now: 900 });
    expect(resumed.items[0]?.startedAt).toBe(900);
  });

  it("thông báo đang rời đi không bị tạm dừng; tạm dừng hoặc đếm tiếp hai lần thì giữ nguyên state", () => {
    const leaving = toastReducer(pushAll(["A"]), { type: "leave", id: 1 });
    const paused = toastReducer(leaving, { type: "pause", now: 2000 });
    expect(paused.items[0]).toEqual(leaving.items[0]);
    expect(toastReducer(paused, { type: "pause", now: 2500 })).toBe(paused);
    const resumed = toastReducer(paused, { type: "resume", now: 3000 });
    expect(toastReducer(resumed, { type: "resume", now: 3500 })).toBe(resumed);
  });
});
