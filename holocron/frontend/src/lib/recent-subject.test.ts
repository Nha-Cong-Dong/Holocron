import { describe, expect, it } from "vitest";

import { RECENT_SUBJECT_STORAGE_KEY, readRecentSubject, writeRecentSubject } from "./recent-subject";
import type { KeyValueStorage } from "./theme";

function memoryStorage(initial: Record<string, string> = {}): KeyValueStorage & { data: Map<string, string> } {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => {
      data.set(key, value);
    },
  };
}

const throwing: KeyValueStorage = {
  getItem: () => {
    throw new Error("SecurityError");
  },
  setItem: () => {
    throw new Error("QuotaExceededError");
  },
};

describe("readRecentSubject", () => {
  it("đọc mã môn đã lưu", () => {
    expect(readRecentSubject(memoryStorage({ [RECENT_SUBJECT_STORAGE_KEY]: "VXL01" }))).toBe("VXL01");
  });

  it("không có kho, chưa lưu hoặc mã sai mẫu ⇒ null", () => {
    expect(readRecentSubject(null)).toBeNull();
    expect(readRecentSubject(memoryStorage())).toBeNull();
    expect(readRecentSubject(memoryStorage({ [RECENT_SUBJECT_STORAGE_KEY]: "vxl01" }))).toBeNull();
    expect(readRecentSubject(memoryStorage({ [RECENT_SUBJECT_STORAGE_KEY]: "<script>" }))).toBeNull();
  });

  it("kho ném lỗi ⇒ null", () => {
    expect(readRecentSubject(throwing)).toBeNull();
  });
});

describe("writeRecentSubject", () => {
  it("ghi mã đúng mẫu", () => {
    const storage = memoryStorage();
    expect(writeRecentSubject(storage, "MAU101")).toBe(true);
    expect(storage.data.get(RECENT_SUBJECT_STORAGE_KEY)).toBe("MAU101");
  });

  it("mã sai mẫu, không có kho hoặc kho ném lỗi ⇒ false", () => {
    const storage = memoryStorage();
    expect(writeRecentSubject(storage, "x")).toBe(false);
    expect(storage.data.size).toBe(0);
    expect(writeRecentSubject(null, "MAU101")).toBe(false);
    expect(writeRecentSubject(throwing, "MAU101")).toBe(false);
  });
});
