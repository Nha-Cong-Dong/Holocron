"use client";

import { createContext, useContext } from "react";

import type { RuntimeState } from "@/lib/runtime";

export interface ShellContextValue {
  runtime: RuntimeState;
  /** Gọi lại `GET /v1/runtime` (nút "Kiểm tra lại"). */
  refreshRuntime: () => void;
  /** Màn gọi LLM báo kết quả để thanh trạng thái hiện "lỗi tạm thời, thử lại" khi gặp 502 `LLM_UPSTREAM_ERROR`. */
  reportLlmCall: (outcome: "upstream_error" | "ok") => void;
  /** Môn đang chọn ở thanh bên; `null` khi chưa chọn. */
  subjectCode: string | null;
  selectSubject: (subjectCode: string) => void;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const value = useContext(ShellContext);
  if (value === null) {
    throw new Error("useShell chỉ dùng được bên trong AppShell.");
  }
  return value;
}
