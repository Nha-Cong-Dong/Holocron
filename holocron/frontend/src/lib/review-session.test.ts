/** Phiên xem một trang: mốc `CONFIRM_GUARD_MS` đếm lại khi trang mới hiện hay vẽ lại; khoá đang gửi (I5). */

import { describe, expect, it } from "vitest";
import { confirmClick } from "./review-confirm";
import {
  beginSend,
  CONFIRM_GUARD_MS,
  endSend,
  guardRemainingMs,
  msSinceShown,
  reviewPageKey,
  reviewSessionFor,
} from "./review-session";

describe("reviewSessionFor", () => {
  it("cùng khoá giữ phiên; đổi version (lưu dòng) hay đổi trang ⇒ phiên mới, đếm lại từ đầu", () => {
    const first = reviewSessionFor(null, reviewPageKey(3, 1), 0);
    expect(first).toEqual({ pageKey: "3:1", shownAt: 0, sending: false });
    expect(reviewSessionFor(first, reviewPageKey(3, 1), 5000)).toBe(first);
    expect(reviewSessionFor(first, reviewPageKey(3, 2), 5000)).toEqual({
      pageKey: "3:2",
      shownAt: 5000,
      sending: false,
    });
    expect(reviewSessionFor(first, reviewPageKey(4, 1), 5000)).toEqual({
      pageKey: "4:1",
      shownAt: 5000,
      sending: false,
    });
  });

  it("phiên mới không mang trạng thái đang gửi của phiên cũ", () => {
    const sending = beginSend(reviewSessionFor(null, "1:1", 0));
    expect(reviewSessionFor(sending, "1:2", 100).sending).toBe(false);
  });
});

describe("mốc thời gian của trang", () => {
  it("đếm từ lúc trang hiện, không âm", () => {
    const session = reviewSessionFor(null, "1:1", 1000);
    expect(msSinceShown(session, 1000)).toBe(0);
    expect(msSinceShown(session, 900)).toBe(0);
    expect(msSinceShown(session, 1000 + CONFIRM_GUARD_MS)).toBe(CONFIRM_GUARD_MS);
  });

  it("thời gian còn phải chờ về 0 đúng ở CONFIRM_GUARD_MS", () => {
    const session = reviewSessionFor(null, "1:1", 1000);
    expect(guardRemainingMs(session, 1000)).toBe(CONFIRM_GUARD_MS);
    expect(guardRemainingMs(session, 1000 + CONFIRM_GUARD_MS - 1)).toBe(1);
    expect(guardRemainingMs(session, 1000 + CONFIRM_GUARD_MS)).toBe(0);
    expect(guardRemainingMs(session, 1000 + CONFIRM_GUARD_MS * 10)).toBe(0);
  });
});

describe("khoá đang gửi", () => {
  it("beginSend bật, endSend tắt, giữ mốc thời gian", () => {
    const session = reviewSessionFor(null, "2:1", 50);
    const sending = beginSend(session);
    expect(sending).toEqual({ pageKey: "2:1", shownAt: 50, sending: true });
    expect(endSend(sending)).toEqual(session);
  });
});

describe("ngưỡng CONFIRM_GUARD_MS tuyệt đối (quyết định 2026-10-29 câu 17 A: 0,6 s)", () => {
  const PAGE = { pageBlock: null, hasSkippedLines: false, appendDraft: false, editing: false, busy: false };

  function acceptedAt(shownAt: number, now: number): boolean {
    return confirmClick(reviewSessionFor(null, "1:1", shownAt), PAGE, now).accepted;
  }

  it("hằng đúng 600 ms", () => {
    expect(CONFIRM_GUARD_MS).toBe(600);
  });

  it("trang hiện lúc 1000: bấm lúc 1599 bị bỏ, lúc 1600 được nhận", () => {
    const session = reviewSessionFor(null, "1:1", 1000);
    expect(guardRemainingMs(session, 1599)).toBe(1);
    expect(guardRemainingMs(session, 1600)).toBe(0);
    expect(acceptedAt(1000, 1599)).toBe(false);
    expect(acceptedAt(1000, 1600)).toBe(true);
  });

  it("trang hiện lúc 0: bấm lúc 100 bị bỏ", () => {
    expect(guardRemainingMs(reviewSessionFor(null, "1:1", 0), 100)).toBe(500);
    expect(acceptedAt(0, 100)).toBe(false);
  });
});
