import { describe, expect, it } from "vitest";

import { COPY_FEEDBACK_MS, COPY_LABEL_VI, copyText } from "@/lib/runtime";

describe("sao chép thư mục log", () => {
  it("thành công thì trả copied và ghi đúng chuỗi", async () => {
    const written: string[] = [];
    const state = await copyText(
      {
        writeText: (t) => {
          written.push(t);
          return Promise.resolve();
        },
      },
      "C:/logs",
    );
    expect(state).toBe("copied");
    expect(written).toEqual(["C:/logs"]);
  });

  it("không có clipboard thì failed", async () => {
    expect(await copyText(undefined, "x")).toBe("failed");
    expect(await copyText(null, "x")).toBe("failed");
  });

  it("lỗi đồng bộ hoặc bị từ chối thì failed", async () => {
    const sync = {
      writeText: (): Promise<void> => {
        throw new Error("chặn");
      },
    };
    const rejected = { writeText: (): Promise<void> => Promise.reject(new Error("từ chối")) };
    expect(await copyText(sync, "x")).toBe("failed");
    expect(await copyText(rejected, "x")).toBe("failed");
  });

  it("nhãn và thời gian phản hồi", () => {
    expect(COPY_LABEL_VI).toEqual({ idle: "Sao chép", copied: "Đã sao chép", failed: "Không sao chép được" });
    expect(COPY_FEEDBACK_MS).toBe(2000);
  });
});
