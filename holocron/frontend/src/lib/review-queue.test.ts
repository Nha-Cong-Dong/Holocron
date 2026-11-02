/** Hàng đợi xem lại theo `pages[]` của tài liệu và điều kiện "xong" theo `status` (màn 6). */

import { describe, expect, it } from "vitest";

import type { DocumentStatusView, PageStatusView, PageSummaryView } from "./documents-data";
import {
  awaitingSettle,
  NOT_SETTLED_VI,
  nextAfterConfirm,
  queueNeighbors,
  queuePosition,
  remainingWorkMessages,
  reviewOutcome,
  reviewProgress,
  reviewQueue,
} from "./review-queue";

function page(pageNumber: number, status: PageStatusView, confirmed = false): PageSummaryView {
  return { pageNumber, status, tierUsed: null, reviewReasons: [], confirmed };
}

function doc(status: DocumentStatusView, pages: PageSummaryView[]) {
  return { status, pages };
}

describe("trang bỏ mềm (dropped) bị loại khỏi hàng đợi và việc còn lại (§5.7)", () => {
  const dropped = (pageNumber: number, status: PageStatusView, confirmed = false): PageSummaryView => ({
    ...page(pageNumber, status, confirmed),
    dropped: true,
  });

  it("hàng đợi và tiến độ không đếm trang needs_review đã bỏ mềm", () => {
    const pages = [
      page(1, "needs_review"),
      dropped(2, "needs_review"),
      page(3, "needs_review", true),
      dropped(4, "ok"),
    ];
    expect(reviewQueue({ pages })).toEqual([1]);
    expect(reviewProgress({ pages })).toEqual({ done: 1, total: 2 });
  });

  it("việc còn lại không đếm trang bỏ mềm (không chặn việc chốt)", () => {
    const outcome = reviewOutcome(doc("review", [page(1, "ok", true), dropped(2, "needs_review"), dropped(3, "ok")]));
    expect(outcome).toEqual({
      kind: "remaining",
      work: { needsReview: 0, okUnconfirmed: 0, pending: 0, failed: 0, unknown: 0 },
    });
  });

  it("awaitingSettle: mọi trang còn lại đã xác nhận hay bỏ mềm thì chờ chốt", () => {
    expect(awaitingSettle(doc("review", [page(1, "ok", true), dropped(2, "needs_review")]))).toBe(true);
    expect(awaitingSettle(doc("review", [page(1, "ok"), dropped(2, "needs_review")]))).toBe(false);
    expect(awaitingSettle(doc("review", [dropped(1, "ok"), dropped(2, "ok")]))).toBe(false);
  });

  it("trang `dropped` vắng (dữ liệu cũ) vẫn tính như trang thường", () => {
    expect(reviewQueue({ pages: [page(1, "needs_review")] })).toEqual([1]);
  });

  it("trang kế bên và đích sau xác nhận bỏ qua trang bỏ mềm", () => {
    const pages = [page(1, "needs_review"), dropped(2, "needs_review"), page(3, "needs_review")];
    const queue = reviewQueue({ pages });
    expect(queue).toEqual([1, 3]);
    expect(queueNeighbors(queue, 2)).toEqual({ previous: 1, next: 3 });
    expect(nextAfterConfirm(queue, 1)).toBe(3);
  });
});

describe("reviewQueue", () => {
  it("chỉ gồm trang cần xem kỹ chưa xác nhận, theo thứ tự backend trả", () => {
    const pages = [
      page(1, "ok"),
      page(2, "needs_review", true),
      page(3, "needs_review"),
      page(4, "failed"),
      page(5, "pending"),
      page(6, "needs_review"),
    ];
    expect(reviewQueue({ pages })).toEqual([3, 6]);
    expect(reviewProgress({ pages })).toEqual({ done: 1, total: 3 });
  });

  it("vị trí và trang kế bên", () => {
    const queue = [3, 6, 9];
    expect(queuePosition(queue, 6)).toBe(2);
    expect(queuePosition(queue, 4)).toBeNull();
    expect(queueNeighbors(queue, 3)).toEqual({ previous: null, next: 6 });
    expect(queueNeighbors(queue, 6)).toEqual({ previous: 3, next: 9 });
    expect(queueNeighbors(queue, 9)).toEqual({ previous: 6, next: null });
    // Trang không trong hàng đợi: trang cần xem gần nhất mỗi phía.
    expect(queueNeighbors(queue, 7)).toEqual({ previous: 6, next: 9 });
    expect(queueNeighbors(queue, 1)).toEqual({ previous: null, next: 3 });
    expect(queueNeighbors([], 1)).toEqual({ previous: null, next: null });
  });

  it("sau khi xác nhận: trang cần xem kế tiếp, hết thì quay về trang cần xem đầu tiên", () => {
    expect(nextAfterConfirm([3, 6, 9], 6)).toBe(9);
    expect(nextAfterConfirm([3, 6, 9], 9)).toBe(3);
    expect(nextAfterConfirm([3], 3)).toBeNull();
    expect(nextAfterConfirm([3, 6], 1)).toBe(3);
  });
});

describe("reviewOutcome", () => {
  it("xong khi và chỉ khi tài liệu confirmed", () => {
    expect(reviewOutcome(doc("confirmed", [page(1, "ok", true), page(2, "failed")]))).toEqual({ kind: "done" });
  });

  it("mọi trang cần xem đã xác nhận nhưng còn trang đọc tốt chưa xác nhận ⇒ chưa xong", () => {
    const outcome = reviewOutcome(doc("review", [page(1, "needs_review", true), page(2, "ok"), page(3, "ok")]));
    expect(outcome).toEqual({
      kind: "remaining",
      work: { needsReview: 0, okUnconfirmed: 2, pending: 0, failed: 0, unknown: 0 },
    });
    if (outcome.kind !== "remaining") throw new Error("phải còn việc");
    expect(remainingWorkMessages(outcome.work)).toEqual([
      "Còn 2 trang đọc tốt chưa xác nhận: bấm “Xác nhận các trang đọc tốt”.",
    ]);
  });

  it("còn trang đang đọc, đọc lỗi hay chưa rõ ⇒ chưa xong, nêu từng việc", () => {
    const outcome = reviewOutcome(
      doc("processing", [page(1, "pending"), page(2, "failed"), page(3, "unknown"), page(4, "needs_review")]),
    );
    if (outcome.kind !== "remaining") throw new Error("phải còn việc");
    expect(outcome.work).toEqual({ needsReview: 1, okUnconfirmed: 0, pending: 1, failed: 1, unknown: 1 });
    expect(remainingWorkMessages(outcome.work)).toHaveLength(4);
  });

  it("tài liệu failed hay review với mọi trang đã xác nhận vẫn chưa xong khi backend chưa chốt", () => {
    const failed = doc("failed", [page(1, "ok", true), page(2, "needs_review", true)]);
    expect(reviewOutcome(failed).kind).toBe("remaining");
    expect(awaitingSettle(failed)).toBe(true);
    expect(awaitingSettle(doc("review", [page(1, "ok", true), page(2, "failed")]))).toBe(true);
    expect(awaitingSettle(doc("review", [page(1, "failed")]))).toBe(false);
    expect(awaitingSettle(doc("review", [page(1, "ok")]))).toBe(false);
    expect(awaitingSettle(doc("confirmed", [page(1, "ok", true)]))).toBe(false);
    expect(NOT_SETTLED_VI).toContain("Tải lại");
  });
});
