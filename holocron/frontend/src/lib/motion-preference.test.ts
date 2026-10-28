import { describe, expect, it } from "vitest";

import { resolveReducedMotion } from "./motion-preference";

describe("resolveReducedMotion", () => {
  it('data-motion="reduce" luôn giảm chuyển động', () => {
    expect(resolveReducedMotion("reduce", false)).toBe(true);
    expect(resolveReducedMotion("reduce", true)).toBe(true);
  });

  it('data-motion="full" thắng prefers-reduced-motion của hệ điều hành', () => {
    expect(resolveReducedMotion("full", true)).toBe(false);
    expect(resolveReducedMotion("full", false)).toBe(false);
  });

  it("không có thuộc tính hoặc giá trị lạ thì theo hệ điều hành", () => {
    expect(resolveReducedMotion(undefined, true)).toBe(true);
    expect(resolveReducedMotion(null, false)).toBe(false);
    expect(resolveReducedMotion("", true)).toBe(true);
    expect(resolveReducedMotion("FULL", true)).toBe(true);
  });
});
