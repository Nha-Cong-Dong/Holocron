"use client";

import { createContext, type ReactNode, useContext, useEffect, useMemo, useReducer } from "react";

import { api, toUiError } from "@/lib/api";
import {
  hasUnsentRows,
  type ImportQueueState,
  type ImportRow,
  importQueueReducer,
  initialImportQueue,
  nextToSend,
  type RowEdit,
} from "@/lib/import-queue";

export type FileImportRow = ImportRow<File>;

export interface ImportQueueApi {
  rows: readonly FileImportRow[];
  /** Số file backend đã nhận theo môn; tăng sau mỗi lần gửi thành công. */
  completions: Readonly<Record<string, number>>;
  add: (subjectCode: string, files: readonly File[]) => void;
  remove: (id: string) => void;
  edit: (id: string, edit: RowEdit) => void;
  start: (subjectCode: string) => void;
  retry: (id: string) => void;
  clearSettled: (subjectCode: string) => void;
}

const ImportQueueContext = createContext<ImportQueueApi | null>(null);

/**
 * Hàng đợi nhập tài liệu sống ở khung app (`AppShell`), không ở trang môn: đổi tab hay rời trang môn trong app vẫn gửi
 * tiếp; quay lại trang môn thì thấy tiến độ. Gửi LẦN LƯỢT từng file (`POST /v1/documents`, §6.5); lỗi một file không
 * dừng hàng. Đóng hoặc tải lại cửa sổ khi còn file chưa gửi xong thì trình duyệt hỏi lại (`beforeunload`).
 */
export function ImportQueueProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    importQueueReducer<File>,
    undefined,
    (): ImportQueueState<File> => initialImportQueue<File>(),
  );

  const next = nextToSend(state);

  // Gửi file kế tiếp khi không có file nào đang gửi. Không huỷ khi effect chạy lại: provider sống suốt phiên app, và
  // kết quả của request đã gửi luôn phải ghi vào đúng dòng của nó.
  useEffect(() => {
    if (next === null) return;
    dispatch({ type: "send_started", id: next.id });
    const title = next.title.trim();
    api
      .importDocument({
        file: next.file,
        subjectCode: next.subjectCode,
        sourceKind: next.sourceKind,
        chapterCode: next.chapterCode ?? "",
        title: title === "" ? null : title,
      })
      .then(
        (outcome) =>
          dispatch({
            type: "send_succeeded",
            id: next.id,
            created: outcome.created,
            documentId: outcome.document.docId,
          }),
        (error: unknown) => dispatch({ type: "send_failed", id: next.id, error: toUiError(error) }),
      );
  }, [next]);

  const unsent = hasUnsentRows(state.rows);
  useEffect(() => {
    if (!unsent) return;
    function onBeforeUnload(event: BeforeUnloadEvent): void {
      event.preventDefault();
      // Trình duyệt cũ chỉ hỏi lại khi `returnValue` được đặt; chữ hiển thị do trình duyệt quyết định.
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [unsent]);

  const value = useMemo<ImportQueueApi>(
    () => ({
      rows: state.rows,
      completions: state.completions,
      add: (subjectCode, files) => dispatch({ type: "add", subjectCode, files }),
      remove: (id) => dispatch({ type: "remove", id }),
      edit: (id, edit) => dispatch({ type: "edit", id, edit }),
      start: (subjectCode) => dispatch({ type: "start", subjectCode }),
      retry: (id) => dispatch({ type: "retry", id }),
      clearSettled: (subjectCode) => dispatch({ type: "clear_settled", subjectCode }),
    }),
    [state.rows, state.completions],
  );

  return <ImportQueueContext.Provider value={value}>{children}</ImportQueueContext.Provider>;
}

/** Hàng đợi nhập tài liệu của khung app; dùng ngoài `ImportQueueProvider` là lỗi lập trình. */
export function useImportQueue(): ImportQueueApi {
  const value = useContext(ImportQueueContext);
  if (value === null) throw new Error("useImportQueue phải nằm trong ImportQueueProvider");
  return value;
}
