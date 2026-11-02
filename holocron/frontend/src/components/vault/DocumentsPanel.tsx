"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, toUiError } from "@/lib/api";
import type { DocumentSummaryView, DocumentView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { createPoller } from "@/lib/poll";

/** Ảnh chụp danh sách tài liệu của môn, kèm `DocumentImport` của các tài liệu đang đọc (để vẽ dải trang). */
export interface DocumentsSnapshot {
  documents: DocumentSummaryView[];
  progress: ReadonlyMap<string, DocumentView>;
}

type ListState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; snapshot: DocumentsSnapshot };

function hasProcessing(snapshot: DocumentsSnapshot): boolean {
  return snapshot.documents.some((document) => document.status === "processing");
}

/**
 * Một lượt đọc: `GET /v1/documents?subject_code=` (số đếm do backend đếm) rồi `GET /v1/documents/{id}` cho từng tài
 * liệu đang đọc (trạng thái từng trang). Các request trong một lượt chạy tuần tự, lượt sau chỉ bắt đầu khi lượt này
 * xong (`lib/poll.ts`).
 */
async function loadSnapshot(subjectCode: string): Promise<DocumentsSnapshot> {
  const documents = await api.listDocuments(subjectCode);
  const progress = new Map<string, DocumentView>();
  for (const summary of documents) {
    if (summary.status !== "processing") continue;
    progress.set(summary.docId, await api.getDocument(summary.docId));
  }
  return { documents, progress };
}

export interface DocumentsSnapshotApi {
  state: ListState;
  /** Lỗi khi tự hỏi lại tiến độ đọc đã bỏ cuộc (lỗi liên tiếp); danh sách cũ vẫn giữ. */
  pollError: UiError | null;
  /** Đọc lại danh sách ngay; còn tài liệu đang đọc thì tự hỏi lại theo chu kỳ (`lib/poll.ts`). */
  reload: () => void;
}

/**
 * Danh sách tài liệu của một môn, tự hỏi lại khi có tài liệu đang đọc. Dùng chung cho `/vault` và tab Tài liệu của
 * trang môn.
 */
export function useDocumentsSnapshot(subjectCode: string): DocumentsSnapshotApi {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [pollError, setPollError] = useState<UiError | null>(null);
  const loads = useRef(createLatestCounter());

  const poller = useMemo(
    () =>
      createPoller<DocumentsSnapshot>({
        load: () => loadSnapshot(subjectCode),
        shouldContinue: hasProcessing,
        onValue: (snapshot) => {
          setPollError(null);
          setState({ kind: "ready", snapshot });
        },
        onError: (error, gaveUp) => {
          if (gaveUp) setPollError(toUiError(error));
        },
      }),
    [subjectCode],
  );

  const reload = useCallback(() => {
    poller.stop();
    const ticket = loads.current.begin();
    setState((current) => (current.kind === "ready" ? current : { kind: "loading" }));
    loadSnapshot(subjectCode)
      .then((snapshot) => {
        if (!loads.current.isLatest(ticket)) return;
        setPollError(null);
        setState({ kind: "ready", snapshot });
        if (hasProcessing(snapshot)) poller.start();
      })
      .catch((error: unknown) => {
        if (loads.current.isLatest(ticket)) setState({ kind: "error", error: toUiError(error) });
      });
  }, [poller, subjectCode]);

  useEffect(() => {
    reload();
    const counter = loads.current;
    return () => {
      counter.invalidate();
      poller.stop();
    };
  }, [reload, poller]);

  return { state, pollError, reload };
}
