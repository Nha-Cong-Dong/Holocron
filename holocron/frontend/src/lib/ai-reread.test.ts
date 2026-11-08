import { describe, expect, it } from "vitest";

import {
  AI_REREAD_ERRORS,
  aiRereadErrorInfo,
  eligibleBlocks,
  GATE_REASON_MESSAGE_VI,
  isCropRef,
  mismatchCellKey,
  mismatchCells,
  missedTokens,
  NO_RECIPIENT_VI,
  parseAiRereadProposal,
  parsePageBlocks,
  rereadButtonLabel,
  rereadReasons,
  sendDisclosure,
  vlmRecipientLabel,
} from "@/lib/ai-reread";
import { classifyHttpError } from "@/lib/errors";

const CROP = "a".repeat(64);

function rawBlock(id: string, flags: string[], extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { block_id: id, type: "table", flags, crop_ref: CROP, text: null, ocr_text: null, ...extra };
}

function rawProposal(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    proposal_id: "prp_0123456789ab",
    document_id: "doc1",
    page_number: 2,
    block_id: "b1",
    page_version: 3,
    status: "accepted_text",
    reason: "ok",
    blocks: [{ type: "paragraph", text: "xin chào", rows: null, flags: [] }],
    mismatches: [],
    ocr_text: "xin chao",
    input_sha256: "b".repeat(64),
    from_cache: false,
    recipient_label: "Gemini",
    ...overrides,
  };
}

describe("chọn khối đủ điều kiện", () => {
  const raw = {
    blocks: [
      rawBlock("b1", ["table_unresolved"]),
      rawBlock("b2", ["boxed"]),
      rawBlock("b3", ["code_layout_uncertain", "boxed"], { type: "code" }),
      rawBlock("b4", ["low_text_contrast"], { type: "paragraph", crop_ref: null }),
      rawBlock("b5", []),
      { block_id: 7 },
      "lạ",
    ],
  };

  it("chỉ giữ khối có một trong ba cờ, theo thứ tự backend", () => {
    const parsed = parsePageBlocks(raw);
    expect(parsed).not.toBeNull();
    expect(eligibleBlocks(parsed ?? []).map((block) => block.blockId)).toEqual(["b1", "b3", "b4"]);
  });

  it("bỏ khối sai dạng, thân sai dạng thành null", () => {
    expect(parsePageBlocks(raw)?.length).toBe(5);
    expect(parsePageBlocks({ blocks: "x" })).toBeNull();
    expect(parsePageBlocks(null)).toBeNull();
  });

  it("crop_ref sai mẫu thành null, không ghép vào đường dẫn", () => {
    const parsed = parsePageBlocks({ blocks: [rawBlock("b1", ["table_unresolved"], { crop_ref: "../x" })] });
    expect(parsed?.[0]?.cropRef).toBeNull();
    expect(isCropRef(CROP)).toBe(true);
    expect(isCropRef("../x")).toBe(false);
  });

  it("nêu lý do bằng tiếng Việt", () => {
    const block = parsePageBlocks({
      blocks: [rawBlock("b1", ["table_unresolved", "low_text_contrast", "boxed"])],
    })?.[0];
    expect(block).toBeDefined();
    if (block !== undefined) expect(rereadReasons(block)).toHaveLength(2);
  });
});

describe("kiểm dạng đề xuất", () => {
  it("nhận đề xuất hợp lệ", () => {
    const proposal = parseAiRereadProposal(rawProposal());
    expect(proposal?.blocks[0]?.text).toBe("xin chào");
    expect(proposal?.page_version).toBe(3);
  });

  it("rejected mà vẫn có khối chữ AI thì bị loại (I3)", () => {
    expect(parseAiRereadProposal(rawProposal({ status: "rejected", reason: "cer" }))).toBeNull();
  });

  it("rejected không khối là hợp lệ; accepted không khối bị loại", () => {
    expect(parseAiRereadProposal(rawProposal({ status: "rejected", reason: "cer", blocks: [] }))?.status).toBe(
      "rejected",
    );
    expect(parseAiRereadProposal(rawProposal({ blocks: [] }))).toBeNull();
  });

  it("khối vừa có text vừa có rows (hoặc thiếu cả hai) bị loại", () => {
    const both = [{ type: "table", text: "x", rows: [["a"]], flags: [] }];
    const neither = [{ type: "table", text: null, rows: null, flags: [] }];
    expect(parseAiRereadProposal(rawProposal({ blocks: both }))).toBeNull();
    expect(parseAiRereadProposal(rawProposal({ blocks: neither }))).toBeNull();
  });

  it("lý do cổng lạ hoặc thân sai dạng thành null", () => {
    expect(parseAiRereadProposal(rawProposal({ reason: "la" }))).toBeNull();
    expect(parseAiRereadProposal(null)).toBeNull();
  });

  it("mọi lý do cổng đều có thông điệp", () => {
    for (const message of Object.values(GATE_REASON_MESSAGE_VI)) expect(message.length).toBeGreaterThan(10);
  });
});

describe("tô chỗ lệch theo dữ liệu backend", () => {
  const mismatches = [
    { mismatch_id: "vmm_000000000001", token_kind: "number" as const, row: 1, col: 2, vlm_value: "9", ocr_value: null },
    {
      mismatch_id: "vmm_000000000002",
      token_kind: "code" as const,
      row: null,
      col: null,
      vlm_value: null,
      ocr_value: "0x1F",
    },
  ];

  it("ô lệch lấy đúng row/col backend trả", () => {
    expect([...mismatchCells(mismatches)]).toEqual([mismatchCellKey(1, 2)]);
  });

  it("token AI bỏ sót chỉ gồm mục chỉ có ocr_value", () => {
    expect(missedTokens(mismatches)).toEqual(["0x1F"]);
  });
});

describe("ánh xạ mã lỗi sang thông điệp", () => {
  it("mọi mã có thông điệp tiếng Việt và một việc làm tiếp", () => {
    for (const info of Object.values(AI_REREAD_ERRORS)) {
      expect(info.message.length).toBeGreaterThan(10);
      expect(["settings", "reread", "reload"]).toContain(info.next);
    }
  });

  it("chưa cấu hình hoặc AI_MODE_OFF dẫn tới Cài đặt; PROPOSAL_STALE đề nghị đọc lại", () => {
    expect(aiRereadErrorInfo("AI_NOT_CONFIGURED")?.next).toBe("settings");
    expect(aiRereadErrorInfo("AI_MODE_OFF")?.next).toBe("settings");
    expect(aiRereadErrorInfo("PROPOSAL_STALE")?.next).toBe("reread");
  });

  it("mã lạ hoặc vắng thì null", () => {
    expect(aiRereadErrorInfo("LA")).toBeNull();
    expect(aiRereadErrorInfo(undefined)).toBeNull();
  });

  it("lỗi 409 và 503 của luồng đọc lại mang mã, không lộ mã HTTP trong thông điệp", () => {
    const conflict = classifyHttpError(409, { detail: { code: "AI_MODE_OFF" } }, { context: "ai_reread" });
    expect(conflict.code).toBe("AI_MODE_OFF");
    expect(conflict.message).toBe(AI_REREAD_ERRORS.AI_MODE_OFF.message);
    const upstream = classifyHttpError(503, { detail: { code: "VLM_TIMEOUT" } }, { context: "ai_reread" });
    expect(upstream.code).toBe("VLM_TIMEOUT");
    expect(upstream.message).toBe(AI_REREAD_ERRORS.VLM_TIMEOUT.message);
    expect(upstream.message).not.toMatch(/503|VLM_TIMEOUT/);
  });

  it("409 mã lạ rơi về thông điệp chung, không có code", () => {
    const error = classifyHttpError(409, { detail: { code: "LA" } }, { context: "ai_reread", versioned: true });
    expect(error.code).toBeUndefined();
  });
});

describe("nguồn khối và người nhận ảnh", () => {
  it("giữ source và ẩn khối vlm khỏi danh sách đọc lại", () => {
    const parsed = parsePageBlocks({
      blocks: [
        rawBlock("b1", ["table_unresolved"], { source: "ocr" }),
        rawBlock("b2", ["table_unresolved"], { source: "vlm" }),
      ],
    });
    expect(parsed?.map((block) => block.source)).toEqual(["ocr", "vlm"]);
    expect(eligibleBlocks(parsed ?? []).map((block) => block.blockId)).toEqual(["b1"]);
  });

  const role = (name: "vlm" | "llm", label: string | null) => ({
    role: name,
    auto_send_allowed: label !== null,
    reason: null,
    provider_id: label === null ? null : "p1",
    recipient_label: label,
    local: false,
  });

  it("lấy nhãn của vai vlm, không lấy vai llm", () => {
    expect(vlmRecipientLabel({ roles: [role("vlm", "Gemini"), role("llm", "Khác")] })).toBe("Gemini");
  });

  it("chưa cấu hình ⇒ null", () => {
    expect(vlmRecipientLabel({ roles: [role("vlm", null), role("llm", "Khác")] })).toBeNull();
    expect(vlmRecipientLabel(null)).toBeNull();
  });

  it("câu báo và nút nêu nhãn người nhận", () => {
    expect(sendDisclosure("Gemini")).toContain("Ảnh vùng này sẽ được gửi tới Gemini");
    expect(rereadButtonLabel("Gemini")).toContain("Gemini");
    expect(rereadButtonLabel(null)).toBe("Đọc lại bằng AI");
    expect(NO_RECIPIENT_VI).not.toBe("");
  });
});
