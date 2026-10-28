/** Phiên xem một trang: mốc `CONFIRM_GUARD_MS` đếm lại khi trang mới hiện hay vẽ lại; khoá đang gửi (I5). */

import { describe, expect, it } from "vitest";

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
