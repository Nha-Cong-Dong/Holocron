/** Phiên xem một trang: khoá Enter khi trang mới hiện hay vẽ lại; phím vừa mở khoá không tự xác nhận (I5). */

import { describe, expect, it } from "vitest";

import { REVIEW_ENTER_GUARD_MS, type ReviewKeyInput, reviewKeyAction } from "./review-keys";
import { applyInteraction, reviewKeydown, reviewPageKey, reviewSessionFor } from "./review-session";

const KEY = { repeat: false, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false };

const PAGE: Omit<
  ReviewKeyInput,
  "key" | "repeat" | "shiftKey" | "altKey" | "ctrlKey" | "metaKey" | "armed" | "msSinceShown"
> = {
  focus: "region",
  overlayOpen: false,
  busy: false,
  hasDraft: false,
  confirmBlock: null,
  hasSkippedLines: false,
  canEdit: true,
  hasPrevious: true,
  hasNext: true,
};

/** Bấm `key` lúc `now` trong phiên; trả hành động và phiên sau phím. */
function press(
  session: ReturnType<typeof reviewSessionFor>,
  key: string,
  now: number,
  extra: Partial<typeof KEY> = {},
) {
  const { gate, next } = reviewKeydown(session, { key, ...KEY, ...extra }, now);
  const action = reviewKeyAction({ ...PAGE, key, ...KEY, ...extra, ...gate });
  return { action, next };
}

describe("reviewSessionFor", () => {
  it("cùng khoá giữ phiên; đổi version (lưu dòng) hay đổi trang ⇒ phiên mới, khoá lại", () => {
    const first = applyInteraction(reviewSessionFor(null, reviewPageKey(3, 1), 0), { type: "pointerdown" });
    expect(first.armed).toBe(true);
    expect(reviewSessionFor(first, reviewPageKey(3, 1), 5000)).toBe(first);
    expect(reviewSessionFor(first, reviewPageKey(3, 2), 5000)).toEqual({ pageKey: "3:2", shownAt: 5000, armed: false });
    expect(reviewSessionFor(first, reviewPageKey(4, 1), 5000)).toEqual({ pageKey: "4:1", shownAt: 5000, armed: false });
  });
});

describe("Enter theo phiên", () => {
  it("trang mới hiện: Enter không xác nhận dù đã lâu", () => {
    const session = reviewSessionFor(null, "1:1", 0);
    expect(press(session, "Enter", 10_000).action).toBeNull();
  });

  it("phím vừa mở khoá không tự xác nhận; Enter sau đó (đủ thời gian) thì xác nhận", () => {
    let session = reviewSessionFor(null, "1:1", 0);
    const arrow = press(session, "ArrowDown", 1000);
    expect(arrow.next.armed).toBe(true);
    session = arrow.next;
    expect(press(session, "Enter", 2000).action).toEqual({ type: "confirm" });
  });

  it("đã mở khoá nhưng Enter sớm hơn 600 ms kể từ lúc trang hiện ⇒ không xác nhận", () => {
    const session = applyInteraction(reviewSessionFor(null, "1:1", 1000), { type: "pointerdown" });
    expect(press(session, "Enter", 1000 + REVIEW_ENTER_GUARD_MS - 1).action).toBeNull();
    expect(press(session, "Enter", 1000 + REVIEW_ENTER_GUARD_MS).action).toEqual({ type: "confirm" });
  });

  it("sau khi lưu dòng (version đổi): phiên mới, Enter không tự xác nhận", () => {
    const armed = applyInteraction(reviewSessionFor(null, "1:1", 0), { type: "pointerdown" });
    const redrawn = reviewSessionFor(armed, "1:2", 5000);
    expect(press(redrawn, "Enter", 20_000).action).toBeNull();
  });

  it("Shift, Ctrl+K rồi Esc, hay '?' không mở khoá: Enter sau đó vẫn không xác nhận", () => {
    let session = reviewSessionFor(null, "1:1", 0);
    session = press(session, "Shift", 1000, { shiftKey: true }).next;
    session = press(session, "k", 1100, { ctrlKey: true }).next;
    session = press(session, "Escape", 1200).next;
    session = press(session, "?", 1300, { shiftKey: true }).next;
    expect(session.armed).toBe(false);
    expect(press(session, "Enter", 5000).action).toBeNull();
    expect(
      press(applyInteraction(session, { type: "pointerdown" }), "Enter", 5000, { shiftKey: true }).action,
    ).toBeNull();
  });
});
