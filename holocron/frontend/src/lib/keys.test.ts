import { describe, expect, it } from "vitest";

import { withContentKeys } from "./keys";

describe("withContentKeys", () => {
  it("key không trùng kể cả khi nội dung trùng, giữ thứ tự", () => {
    const keyed = withContentKeys(["a", "b", "a", "a"], (text) => text);
    expect(keyed.map((k) => k.item)).toEqual(["a", "b", "a", "a"]);
    expect(new Set(keyed.map((k) => k.key)).size).toBe(4);
  });
});
