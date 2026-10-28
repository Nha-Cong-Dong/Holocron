"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import type { DocumentImportOutcome, DocumentSummaryView, DocumentView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { createPoller } from "@/lib/poll";
import { freshRuntime } from "@/lib/runtime";
import { type VaultHrefFor, vaultHref } from "@/lib/vault";

import { DocumentList } from "./DocumentList";
import { ImportForm } from "./ImportForm";
import styles from "./Vault.module.css";

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

interface DocumentsPanelProps {
  subjectCode: string;
  /** Dựng địa chỉ xem lại trang; mặc định `vaultHref` (`/vault?doc=&page=`). Trang môn giữ trong `/subject`. */
  hrefFor?: VaultHrefFor;
}

/** Mục "Tài liệu" của một môn: form nhập PDF và danh sách tài liệu, tự hỏi lại trạng thái khi có tài liệu đang đọc. */
export function DocumentsPanel({ subjectCode, hrefFor = vaultHref }: DocumentsPanelProps) {
  const { runtime } = useShell();
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [pollError, setPollError] = useState<UiError | null>(null);
  const [outcome, setOutcome] = useState<DocumentImportOutcome | null>(null);
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

  const onImported = useCallback(
    (result: DocumentImportOutcome) => {
      setOutcome(result);
      reload();
    },
    [reload],
  );

  return (
    <>
      <section className={styles.panel} aria-labelledby="vault-import">
        <h2 id="vault-import">Nhập tài liệu PDF</h2>
        <p className={styles.muted}>
          Mỗi file là một chương. Hệ thống đọc từng trang; trang nào đọc chưa chắc chắn sẽ được đánh dấu để bạn xem lại
          và xác nhận trước khi văn bản vào kho tri thức.
        </p>
        <CapabilityNotice runtime={freshRuntime(runtime)} name="ocr" />
        <ImportForm subjectCode={subjectCode} onImported={onImported} />
        {outcome !== null && <ImportOutcome outcome={outcome} />}
      </section>

      <section className={styles.panel} aria-labelledby="vault-documents">
        <div className={styles.panelHeader}>
          <h2 id="vault-documents">Tài liệu của môn {subjectCode}</h2>
          {state.kind === "ready" && (
            <button type="button" className={`${styles.secondary} ${styles.small}`} onClick={reload}>
              Tải lại
            </button>
          )}
        </div>
        {state.kind === "loading" && <Loading label="Đang tải danh sách tài liệu…" />}
        {state.kind === "error" && <ErrorNotice error={state.error} onRetry={reload} onReload={reload} />}
        {pollError !== null && (
          <>
            <p className={styles.warningNote}>
              Đã dừng tự cập nhật tiến độ đọc vì không hỏi được backend nhiều lần liền.
            </p>
            <ErrorNotice error={pollError} onRetry={reload} onReload={reload} />
          </>
        )}
        {state.kind === "ready" && state.snapshot.documents.length === 0 && (
          <EmptyState title="Chưa có tài liệu nào trong môn này.">
            Kéo file PDF vào ô ở trên, hoặc bấm “Chọn file PDF”, rồi nhập mã chương và loại tài liệu.
          </EmptyState>
        )}
        {state.kind === "ready" && state.snapshot.documents.length > 0 && (
          <DocumentList snapshot={state.snapshot} onChanged={reload} hrefFor={hrefFor} />
        )}
      </section>
    </>
  );
}

function ImportOutcome({ outcome }: { outcome: DocumentImportOutcome }) {
  const { document } = outcome;
  if (outcome.created) {
    return (
      <output className={styles.successNote}>
        Đã nhận “{document.title}” ({document.pageCount} trang). Hệ thống đang đọc; tiến độ hiện ở danh sách bên dưới.
      </output>
    );
  }
  return (
    <output className={styles.note}>
      “{document.title}” đã được nhập vào môn này từ trước (cùng nội dung file), nên hệ thống không đọc lại. Xem tài
      liệu đó ở danh sách bên dưới.
    </output>
  );
}
