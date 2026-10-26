"use client";

import { createContext, useContext } from "react";

import type { RuntimeState } from "@/lib/runtime";
import type { SubjectsState } from "@/lib/subjects";

export interface ShellContextValue {
  runtime: RuntimeState;
  /** Gọi lại `GET /v1/runtime` (nút "Kiểm tra lại"). */
  refreshRuntime: () => void;
  /** Màn gọi LLM báo kết quả để thanh trạng thái hiện "lỗi tạm thời, thử lại" khi gặp 502 `LLM_UPSTREAM_ERROR`. */
  reportLlmCall: (outcome: "upstream_error" | "ok") => void;
  /** Danh sách môn (`GET /v1/subjects`), tải một lần cho cả app. */
  subjects: SubjectsState;
  /** Tải lại danh sách môn. */
  reloadSubjects: () => void;
  /** Môn đang chọn ở thanh bên; `null` khi chưa chọn. */
  subjectCode: string | null;
  selectSubject: (subjectCode: string) => void;
  /** Đang mở hộp lệnh hoặc bảng phím tắt: màn hình con không được xử lý phím tắt riêng. */
  overlayOpen: boolean;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const value = useContext(ShellContext);
  if (value === null) {
    throw new Error("useShell chỉ dùng được bên trong AppShell.");
  }
  return value;
}
