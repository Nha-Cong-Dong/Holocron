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

describe("sửa nhãn chunk (vault_labels)", () => {
  const VERSIONED = { context: "vault_labels", versioned: true } as const;

  it("404: chunk không còn (tài liệu gốc đã bị xoá), không chép detail", () => {
    const error = classifyHttpError(404, { detail: "không tìm thấy chunk" }, VERSIONED);
    expect(error.kind).toBe("not_found");
    expect(error.message).toContain("đã bị xoá");
    expect(error.message).not.toContain("chunk");
  });

  it("409: lệch phiên bản, có nút tải lại bản mới nhất", () => {
    const error = classifyHttpError(409, { detail: "expected_version 3, hiện tại 4" }, VERSIONED);
    expect(error.kind).toBe("version_conflict");
    expect(error.action).toBe("reload");
    expect(error.message).toContain("Nhãn");
    expect(error.message).not.toContain("expected_version");
  });

  it("422 dạng [{loc, msg, type}]: câu nêu luật của chương và khoá dạng bài, không lộ tên field", () => {
    const body = {
      detail: [{ loc: ["body", "topic_keys", 0], msg: "String should match pattern", type: "string_pattern_mismatch" }],
    };
    const error = classifyHttpError(422, body, VERSIONED);
    expect(error.kind).toBe("validation");
    expect(error.action).toBe("none");
    expect(error.message).toContain("ch2.1");
    expect(error.message).toContain("32 khoá");
    expect(error.message).not.toContain("topic_keys");
    expect(error.message).not.toContain("pattern");
  });
});

describe("xác nhận trang và xác nhận tài liệu có câu riêng", () => {
  it("xác nhận trang 409: nhắc cả trường hợp trang không có dòng nào có văn bản; có nút tải lại", () => {
    const error = classifyHttpError(409, { detail: "x" }, { context: "page_confirm", versioned: true });
    expect(error.message).toContain("không có dòng nào có văn bản");
    expect(error.action).toBe("reload");
  });

  it("xác nhận tài liệu: 409 là còn trang đang đọc; 404 là tài liệu không còn", () => {
    const conflict = classifyHttpError(409, { detail: "còn trang pending" }, { context: "document_confirm" });
    expect(conflict.message).toContain("còn trang đang đọc");
    expect(conflict.action).toBe("reload");
    const missing = classifyHttpError(404, { detail: "x" }, { context: "document_confirm" });
    expect(missing.kind).toBe("not_found");
    expect(missing.message).toContain("tài liệu");
  });

  it("sửa dòng 422: chỉ nói về độ dài dòng, không còn vế giới hạn của kho tri thức", () => {
    const error = classifyHttpError(422, { detail: [] }, { context: "document_review", versioned: true });
    expect(error.message).toContain("2000 ký tự");
    expect(error.message).not.toContain("kho tri thức");
  });
});
