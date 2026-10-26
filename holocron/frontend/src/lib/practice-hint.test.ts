/** Gợi ý cho slot lý thuyết không sinh được: nói đúng tình trạng, không hướng người dùng sang việc không giúp được. */

import { describe, expect, it } from "vitest";

import { PAIR_SCREEN_MISSING_VI, VAULT_HINT_VI } from "./practice";

describe("VAULT_HINT_VI", () => {
  it("nêu chương chưa có đủ cặp khái niệm đã xác nhận và màn tạo cặp chưa có", () => {
    expect(VAULT_HINT_VI).toContain("cặp khái niệm đã xác nhận");
    expect(VAULT_HINT_VI).toContain(PAIR_SCREEN_MISSING_VI);
    expect(PAIR_SCREEN_MISSING_VI).toContain("chưa có màn tạo cặp khái niệm");
  });

  it("không gợi ý xác nhận nhãn chunk (việc đó không tạo ra cặp khái niệm)", () => {
    expect(VAULT_HINT_VI).not.toMatch(/nhãn|chunk|đoạn văn bản/u);
  });
});
