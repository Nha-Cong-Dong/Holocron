/**
 * Sửa review T2.1: lần bấm "Khôi phục" bị cờ gửi một lần chặn không phải lỗi, và `isDropped` chỉ có một định nghĩa dùng
 * chung giữa `review-queue.ts` và `dropped-pages.ts`.
 */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import type { PageSummaryView } from "./documents-data";
import { isDropped, RESTORE_DONE_VI, RESTORE_FAILED_STATUS_VI, restoreStatusText } from "./dropped-pages";
import { reviewProgress, reviewQueue } from "./review-queue";

describe("restoreStatusText", () => {
  it("lần bấm bị chặn không đặt thông báo (không báo thất bại)", () => {
    expect(restoreStatusText("blocked")).toBeNull();
  });

  it("thành công và thất bại có câu riêng", () => {
    expect(restoreStatusText("done")).toBe(RESTORE_DONE_VI);
    expect(restoreStatusText("failed")).toBe(RESTORE_FAILED_STATUS_VI);
    expect(RESTORE_FAILED_STATUS_VI).not.toBe(RESTORE_DONE_VI);
  });
});

describe("isDropped dùng chung", () => {
  it("review-queue không còn bản `isDropped` riêng", () => {
    const source = readFileSync(new URL("./review-queue.ts", import.meta.url), "utf-8");
    expect(source).not.toMatch(/function isDropped/);
    expect(source).toMatch(/import \{ isDropped \} from "@\/lib\/dropped-pages"/);
  });

  it("hàng đợi và tiến độ loại trang bỏ mềm đúng như `isDropped`", () => {
    const pages: PageSummaryView[] = [
      { pageNumber: 1, status: "needs_review", confirmed: false, tierUsed: null, reviewReasons: [], dropped: true },
      { pageNumber: 2, status: "needs_review", confirmed: false, tierUsed: null, reviewReasons: [] },
      { pageNumber: 3, status: "needs_review", confirmed: true, tierUsed: null, reviewReasons: [], dropped: false },
    ];
    expect(pages.map((page) => isDropped(page))).toEqual([true, false, false]);
    const document = { pages };
    expect(reviewQueue(document)).toEqual([2]);
    expect(reviewProgress(document)).toEqual({ done: 1, total: 2 });
  });
});
