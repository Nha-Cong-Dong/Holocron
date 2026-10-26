"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { Modal } from "@/components/common/Modal";
import { api, toUiError } from "@/lib/api";
import { DOCUMENT_STATUS_LABEL_VI, pageWarnings, sourceKindLabel } from "@/lib/documents";
import type { DocumentStatusView, DocumentSummaryView, DocumentView, PageSummaryView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { vaultHref } from "@/lib/vault";

import type { DocumentsSnapshot } from "./DocumentsPanel";
import { PageStrip } from "./PageStrip";
import styles from "./Vault.module.css";

const STATUS_TONE: Record<DocumentStatusView, "info" | "warning" | "success" | "danger" | "neutral"> = {
  processing: "info",
  review: "warning",
  confirmed: "success",
  failed: "danger",
  unknown: "neutral",
};

const DATE_FORMAT = new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" });

/** Thời điểm tải lên để hiển thị; chuỗi không đọc được thì hiện nguyên văn. */
function formatUploadedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : DATE_FORMAT.format(date);
}

interface DocumentListProps {
  snapshot: DocumentsSnapshot;
  onChanged: () => void;
}

/** Danh sách tài liệu của môn (mới nhất trước, như backend trả), trạng thái bằng chữ và số đếm do backend đếm. */
export function DocumentList({ snapshot, onChanged }: DocumentListProps) {
  return (
    <ul className={styles.docList}>
      {snapshot.documents.map((document) => (
        <DocumentItem
          key={document.docId}
          summary={document}
          progress={snapshot.progress.get(document.docId) ?? null}
          onChanged={onChanged}
        />
      ))}
    </ul>
  );
}

interface DocumentItemProps {
  summary: DocumentSummaryView;
  progress: DocumentView | null;
  onChanged: () => void;
}

function DocumentItem({ summary, progress, onChanged }: DocumentItemProps) {
  const ids = useId();
  const [warningsOpen, setWarningsOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const processing = summary.status === "processing";

  return (
    <li className={styles.docItem} aria-labelledby={`${ids}-title`}>
      <div className={styles.docHeader}>
        <h3 id={`${ids}-title`} className={styles.docTitle}>
          {summary.title}
        </h3>
        <span className={styles.badge} data-tone={STATUS_TONE[summary.status]}>
          {DOCUMENT_STATUS_LABEL_VI[summary.status]}
        </span>
      </div>
      <p className={styles.meta}>
        <span>{sourceKindLabel(summary.sourceKind)}</span>
        <span>{summary.pageCount} trang</span>
        <span>Tải lên {formatUploadedAt(summary.uploadedAt)}</span>
      </p>

      {processing ? (
        <div>
          <p className={styles.muted}>
            Còn {summary.pagesPending} trên {summary.pageCount} trang chưa đọc.
          </p>
          {progress !== null && <PageStrip pages={progress.pages} />}
        </div>
      ) : (
        <p className={styles.muted}>
          {summary.pagesNeedsReview} trang cần xem kỹ · {summary.pagesFailed} trang đọc lỗi · {summary.pagesConfirmed}/
          {summary.pageCount} trang đã xác nhận
        </p>
      )}
      {summary.status === "failed" && (
        <p className={styles.warningNote}>
          Backend không mở lại được tài liệu này sau khi đã nhận. Xoá tài liệu rồi nhập lại file.
        </p>
      )}

      <div className={styles.actions}>
        {!processing && summary.status !== "failed" && (
          <Link
            className={`${styles.primary} ${styles.small}`}
            href={vaultHref({ subject: summary.subjectCode, doc: summary.docId, page: 1 })}
          >
            Xem lại và xác nhận trang
          </Link>
        )}
        {!processing && (
          <button
            type="button"
            className={`${styles.secondary} ${styles.small}`}
            aria-expanded={warningsOpen}
            aria-controls={`${ids}-warnings`}
            onClick={() => setWarningsOpen((open) => !open)}
          >
            {warningsOpen ? "Ẩn cảnh báo theo trang" : "Cảnh báo theo trang"}
          </button>
        )}
        <button
          type="button"
          className={`${styles.danger} ${styles.small}`}
          onClick={() => setDeleting(true)}
          disabled={processing}
          title={processing ? "Đang đọc tài liệu, chờ đọc xong rồi xoá" : undefined}
        >
          Xoá tài liệu
        </button>
      </div>
      <div id={`${ids}-warnings`}>
        {warningsOpen && <PageWarningsPanel documentId={summary.docId} subjectCode={summary.subjectCode} />}
      </div>
      <DeleteDialog
        open={deleting}
        title={summary.title}
        documentId={summary.docId}
        onClose={() => setDeleting(false)}
        onDeleted={() => {
          setDeleting(false);
          onChanged();
        }}
        onReload={() => {
          setDeleting(false);
          onChanged();
        }}
      />
    </li>
  );
}

type WarningsState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; document: DocumentView };

/** Cảnh báo từng trang của một tài liệu đã đọc xong: tải `DocumentImport` khi người dùng mở. */
function PageWarningsPanel({ documentId, subjectCode }: { documentId: string; subjectCode: string }) {
  const [state, setState] = useState<WarningsState>({ kind: "loading" });
  const calls = useRef(createLatestCounter());

  const load = useCallback(() => {
    const ticket = calls.current.begin();
    setState({ kind: "loading" });
    api
      .getDocument(documentId)
      .then((document) => {
        if (calls.current.isLatest(ticket)) setState({ kind: "ready", document });
      })
      .catch((error: unknown) => {
        if (calls.current.isLatest(ticket)) setState({ kind: "error", error: toUiError(error) });
      });
  }, [documentId]);

  useEffect(() => {
    load();
    const counter = calls.current;
    return () => counter.invalidate();
  }, [load]);

  if (state.kind === "loading") return <Loading label="Đang tải cảnh báo theo trang…" />;
  if (state.kind === "error") return <ErrorNotice error={state.error} onRetry={load} onReload={load} />;
  const flagged = state.document.pages.filter((page) => pageWarnings(page).length > 0);
  if (flagged.length === 0) {
    return <p className={styles.successNote}>Không trang nào có cảnh báo. Bạn có thể xác nhận cả tài liệu.</p>;
  }
  return (
    <ul className={styles.warningList}>
      {flagged.map((page) => (
        <PageWarningItem key={page.pageNumber} page={page} documentId={documentId} subjectCode={subjectCode} />
      ))}
    </ul>
  );
}

function PageWarningItem({
  page,
  documentId,
  subjectCode,
}: {
  page: PageSummaryView;
  documentId: string;
  subjectCode: string;
}) {
  return (
    <li className={styles.warningPage}>
      <Link href={vaultHref({ subject: subjectCode, doc: documentId, page: page.pageNumber })}>
        Trang {page.pageNumber}
        {page.confirmed ? " (đã xác nhận)" : ""}
      </Link>
      <ul>
        {pageWarnings(page).map((warning) => (
          <li key={warning.key} className={styles.warning} data-tone={warning.tone}>
            {warning.label}
          </li>
        ))}
      </ul>
    </li>
  );
}

interface DeleteDialogProps {
  open: boolean;
  title: string;
  documentId: string;
  onClose: () => void;
  onDeleted: () => void;
  /** Nút "Tải lại bản mới nhất" của lỗi 409 (tài liệu đang đọc): chỉ đọc lại trạng thái, không coi là đã xoá. */
  onReload: () => void;
}

/** Xác nhận xoá (`DELETE /v1/documents/{id}`): xoá cả trang, chunk và cặp khái niệm của tài liệu (§6.5). */
export function DeleteDialog({ open, title, documentId, onClose, onDeleted, onReload }: DeleteDialogProps) {
  const ids = useId();
  const [error, setError] = useState<UiError | null>(null);
  const [busy, setBusy] = useState(false);

  async function remove(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.deleteDocument(documentId);
      onDeleted();
    } catch (caught) {
      setError(toUiError(caught));
    } finally {
      setBusy(false);
    }
  }

  /** Đóng hộp thoại (Esc, "Huỷ"): bỏ lỗi cũ để lần mở sau không hiện lại. */
  function close(): void {
    setError(null);
    onClose();
  }

  function reload(): void {
    setError(null);
    onReload();
  }

  return (
    <Modal open={open} onClose={close} labelledBy={`${ids}-title`}>
      <div className={styles.form}>
        <h2 id={`${ids}-title`}>Xoá tài liệu “{title}”?</h2>
        <p>
          Xoá cả văn bản các trang, phần của tài liệu trong kho tri thức và các cặp khái niệm lấy từ đó. Câu hỏi đã sinh
          vẫn giữ trích dẫn. Muốn đọc lại trang lỗi, xoá rồi nhập lại file.
        </p>
        {error !== null && <ErrorNotice error={error} onRetry={() => void remove()} onReload={reload} />}
        <div className={styles.actions}>
          <button type="button" className={styles.danger} onClick={() => void remove()} disabled={busy}>
            {busy ? "Đang xoá…" : "Xoá tài liệu"}
          </button>
          <button type="button" className={styles.secondary} onClick={close} disabled={busy}>
            Huỷ
          </button>
        </div>
      </div>
    </Modal>
  );
}
