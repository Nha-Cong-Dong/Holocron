import { describe, expect, it } from "vitest";

import { EMPTY_TOAST_QUEUE, MAX_VISIBLE_TOASTS, type ToastQueue, toastReducer } from "./toast-queue";

function pushAll(messages: readonly string[], from: ToastQueue = EMPTY_TOAST_QUEUE): ToastQueue {
  return messages.reduce((state, message) => toastReducer(state, { type: "push", message }), from);
}

describe("toastReducer", () => {
  it("thêm thông báo với id tăng dần", () => {
    const state = pushAll(["Đã nhận 6 khái niệm", "Đã đọc xong 3 file"]);
    expect(state.items).toEqual([
      { id: 1, message: "Đã nhận 6 khái niệm", leaving: false },
      { id: 2, message: "Đã đọc xong 3 file", leaving: false },
    ]);
    expect(state.nextId).toBe(3);
  });

  it("bỏ qua câu rỗng và cắt khoảng trắng hai đầu", () => {
    expect(pushAll(["   "])).toBe(EMPTY_TOAST_QUEUE);
    expect(pushAll(["  Đã lưu  "]).items[0]?.message).toBe("Đã lưu");
  });

  it("không chồng hai bản của cùng một câu đang hiện", () => {
    const once = pushAll(["Đã lưu"]);
    expect(toastReducer(once, { type: "push", message: "Đã lưu" })).toBe(once);
  });

  it("câu giống một thông báo đang rời đi thì vẫn được thêm", () => {
    const leaving = toastReducer(pushAll(["Đã lưu"]), { type: "leave", id: 1 });
    expect(toastReducer(leaving, { type: "push", message: "Đã lưu" }).items).toHaveLength(2);
  });

  it("quá số thông báo hiện cùng lúc thì cái cũ nhất bắt đầu rời đi", () => {
    const messages = Array.from({ length: MAX_VISIBLE_TOASTS + 1 }, (_, index) => `Thông báo ${index + 1}`);
    const state = pushAll(messages);
    expect(state.items.filter((item) => !item.leaving)).toHaveLength(MAX_VISIBLE_TOASTS);
    expect(state.items[0]).toEqual({ id: 1, message: "Thông báo 1", leaving: true });
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
