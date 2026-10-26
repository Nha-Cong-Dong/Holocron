/**
 * Câu lỗi của luồng nhập tài liệu, xem lại trang, xoá tài liệu, tạo môn và Vault (§6.5, §6.6, §6.8): mỗi mã có câu
 * tiếng Việt dễ hiểu theo luồng, không chép `detail` nội bộ của backend, và nút đúng loại.
 */

import { describe, expect, it } from "vitest";
import { classifyHttpError } from "./errors";
import { checkSubjectForm } from "./subjects";

describe("409 theo luồng", () => {
  it("nhập tài liệu trùng ở môn khác: câu dễ hiểu, không có nút tải lại", () => {
    const error = classifyHttpError(409, { detail: "doc_x đã thuộc môn ABC123" }, { context: "document_upload" });
    expect(error.kind).toBe("duplicate");
    expect(error.action).toBe("none");
    expect(error.message).toContain("môn khác");
    expect(error.message).not.toContain("doc_x");
  });

  it("sửa dòng hay xác nhận lệch phiên bản: nút tải lại bản mới nhất", () => {
    const error = classifyHttpError(
      409,
      { detail: "version lệch: hiện tại 4" },
      { context: "document_review", versioned: true },
    );
    expect(error.kind).toBe("version_conflict");
    expect(error.action).toBe("reload");
    expect(error.message).not.toContain("version");
  });

  it("xoá khi đang đọc: nói rõ lý do và cách làm tiếp", () => {
    const error = classifyHttpError(409, { detail: "tài liệu đang đọc" }, { context: "document_delete" });
    expect(error.message).toContain("đang được đọc");
    expect(error.action).toBe("reload");
  });

  it("tạo môn đã có: không có nút thử lại", () => {
    const error = classifyHttpError(409, { detail: "môn đã có" }, { context: "subject_create" });
    expect(error.kind).toBe("duplicate");
    expect(error.action).toBe("none");
  });

  it("409 của luồng cũ không đổi: approve còn BLOCKING vẫn là blocking", () => {
    const error = classifyHttpError(409, { detail: { message: "còn lỗi", issues: ["Ô A1 lệch"] } });
    expect(error.kind).toBe("blocking");
  });
});

describe("404 và 422 theo luồng", () => {
  it("backend chưa có endpoint sửa dòng hay tạo môn: câu nói rõ backend chưa hỗ trợ", () => {
    expect(classifyHttpError(404, { detail: "Not Found" }, { context: "document_review" }).message).toContain(
      "chưa hỗ trợ",
    );
    expect(classifyHttpError(404, { detail: "Not Found" }, { context: "subject_create" }).message).toContain(
      "nhập tài liệu đầu tiên",
    );
  });

  it("nhập tài liệu: 413, 415, 422 có câu riêng", () => {
    expect(classifyHttpError(413, { detail: "x" }, { context: "document_upload" }).message).toContain("50 MiB");
    expect(classifyHttpError(415, { detail: "x" }, { context: "document_upload" }).message).toContain("PDF");
    expect(classifyHttpError(422, { detail: "x" }, { context: "document_upload" }).message).toContain("mật khẩu");
  });
});

describe("checkSubjectForm", () => {
  it("viết hoa mã môn, bỏ tên trống, luôn gửi exam_format", () => {
    expect(checkSubjectForm({ subjectCode: " mau303 ", displayName: "  ", examFormat: "essay" })).toEqual({
      ok: true,
      payload: { subject_code: "MAU303", exam_format: "essay" },
    });
    expect(checkSubjectForm({ subjectCode: "MAU303", displayName: "Môn mẫu", examFormat: "mixed" })).toEqual({
      ok: true,
      payload: { subject_code: "MAU303", exam_format: "mixed", display_name: "Môn mẫu" },
    });
  });

  it("mã sai mẫu hay tên quá 200 ký tự thì báo lỗi theo ô", () => {
    const bad = checkSubjectForm({ subjectCode: "M1", displayName: "x".repeat(201), examFormat: "mcq" });
    expect(bad.ok).toBe(false);
    if (!bad.ok) {
      expect(bad.errors.code).toBeDefined();
      expect(bad.errors.name).toBeDefined();
    }
  });
});
