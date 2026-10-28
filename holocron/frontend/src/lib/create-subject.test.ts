import { describe, expect, it } from "vitest";
import type { Subject, SubjectCreate } from "@/types/holocron";
import { type CreateSubjectFn, createWithRetry } from "./create-subject";
import { ApiError, classifyHttpError } from "./errors";

function subjectFor(payload: SubjectCreate): Subject {
  return {
    subject_code: payload.subject_code,
    display_name: payload.display_name ?? payload.subject_code,
    exam_format: payload.exam_format ?? "mixed",
    version: 1,
    created_at: "2026-10-29T00:00:00Z",
    updated_at: "2026-10-29T00:00:00Z",
  };
}

function httpError(status: number, detail: unknown): ApiError {
  return new ApiError(status, classifyHttpError(status, { detail }, { context: "subject_create" }));
}

/** Hàm tạo giả: trả lần lượt kết quả đã định cho từng lần gọi và ghi lại payload. */
function scripted(results: readonly (ApiError | "ok")[]): { create: CreateSubjectFn; calls: SubjectCreate[] } {
  const calls: SubjectCreate[] = [];
  const create: CreateSubjectFn = async (payload) => {
    const result = results[calls.length];
    calls.push(payload);
    if (result === undefined || result === "ok") return subjectFor(payload);
    throw result;
  };
  return { create, calls };
}

const PAYLOAD: SubjectCreate = { subject_code: "VXL01", display_name: "Vi xử lý", exam_format: "mixed" };
const DUPLICATE = httpError(409, "subject_code already exists");

describe("createWithRetry", () => {
  it("thành công ngay: một lần gọi, giữ mã", async () => {
    const { create, calls } = scripted(["ok"]);
    const outcome = await createWithRetry(create, PAYLOAD, []);
    expect(outcome.ok && outcome.subject.subject_code).toBe("VXL01");
    expect(outcome.code).toBe("VXL01");
    expect(calls).toHaveLength(1);
  });

  it("409 rồi thành công: thử lại một lần với mã tăng số, bỏ qua mã đã có", async () => {
    const { create, calls } = scripted([DUPLICATE, "ok"]);
    const outcome = await createWithRetry(create, PAYLOAD, ["VXL02"]);
    expect(outcome).toMatchObject({ ok: true, code: "VXL03" });
    expect(calls.map((call) => call.subject_code)).toEqual(["VXL01", "VXL03"]);
    expect(calls[1]).toEqual({ ...PAYLOAD, subject_code: "VXL03" });
  });

  it("409 hai lần: dừng sau lần thử lại, trả lỗi trùng và mã cuối", async () => {
    const { create, calls } = scripted([DUPLICATE, DUPLICATE, "ok"]);
    const outcome = await createWithRetry(create, PAYLOAD, []);
    expect(outcome.ok).toBe(false);
    expect(!outcome.ok && outcome.error.kind).toBe("duplicate");
    expect(outcome.code).toBe("VXL02");
    expect(calls).toHaveLength(2);
  });

  it("lỗi khác không thử lại", async () => {
    const { create, calls } = scripted([httpError(422, [{ loc: ["body", "subject_code"], msg: "x", type: "x" }])]);
    const outcome = await createWithRetry(create, PAYLOAD, []);
    expect(!outcome.ok && outcome.error.kind).toBe("validation");
    expect(calls).toHaveLength(1);
  });

  it("lỗi máy chủ hoặc lỗi lạ không thử lại", async () => {
    for (const failure of [new ApiError(500, classifyHttpError(500, undefined)), new Error("lỗi lạ")]) {
      const calls: SubjectCreate[] = [];
      const create: CreateSubjectFn = async (payload) => {
        calls.push(payload);
        throw failure;
      };
      const outcome = await createWithRetry(create, PAYLOAD, []);
      expect(outcome.ok).toBe(false);
      expect(calls).toHaveLength(1);
    }
  });

  it("mã sai mẫu hoặc hết số: không thử lại", async () => {
    for (const code of ["vxl", "AB9999"]) {
      const { create, calls } = scripted([DUPLICATE]);
      const outcome = await createWithRetry(create, { ...PAYLOAD, subject_code: code }, []);
      expect(outcome).toMatchObject({ ok: false, code });
      expect(calls).toHaveLength(1);
    }
  });
});
