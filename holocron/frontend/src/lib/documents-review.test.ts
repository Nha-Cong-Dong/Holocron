/** Lưu dòng ở màn xem lại trang: không gửi hai lần khi lần trước chưa xong (Enter rồi bấm "Lưu" liền tay). */

import { describe, expect, it } from "vitest";

import { createSingleFlight, lineSaveDecision } from "./documents";
import { LINE_TEXT_MAX_CHARS } from "./limits";

describe("lineSaveDecision", () => {
  it("đang lưu thì chặn cả Enter lẫn nút Lưu, kể cả khi nội dung hợp lệ", () => {
    expect(lineSaveDecision("dòng mới", true)).toEqual({ kind: "blocked" });
    expect(lineSaveDecision("x".repeat(LINE_TEXT_MAX_CHARS + 1), true)).toEqual({ kind: "blocked" });
  });

  it("không bận: hợp lệ thì lưu; quá dài thì báo lỗi trên ô; dòng trống vẫn lưu (bỏ dòng khỏi văn bản)", () => {
    expect(lineSaveDecision("dòng mới", false)).toEqual({ kind: "save" });
    expect(lineSaveDecision("", false)).toEqual({ kind: "save" });
    const tooLong = lineSaveDecision("x".repeat(LINE_TEXT_MAX_CHARS + 1), false);
    expect(tooLong.kind).toBe("invalid");
  });
});

describe("createSingleFlight", () => {
  it("lần vào thứ hai bị từ chối tới khi lần đầu rời đi", () => {
    const gate = createSingleFlight();
    expect(gate.tryEnter()).toBe(true);
    expect(gate.tryEnter()).toBe(false);
    gate.leave();
    expect(gate.tryEnter()).toBe(true);
  });
});
