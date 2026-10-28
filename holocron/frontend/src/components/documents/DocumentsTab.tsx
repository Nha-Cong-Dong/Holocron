"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { useShell } from "@/components/shell/ShellContext";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { DocumentList } from "@/components/vault/DocumentList";
import { useDocumentsSnapshot } from "@/components/vault/DocumentsPanel";
import { PageReview } from "@/components/vault/PageReview";
import type { UiError } from "@/lib/errors";
import {
  type ImportRowStatus,
  isStaged,
  newlySettled,
  queueProgress,
  readingRows,
  rowsOfSubject,
  settledAnnouncement,
} from "@/lib/import-queue";
import { freshRuntime } from "@/lib/runtime";
import type { SubjectDocumentLocation } from "@/lib/subject-route";
import type { VaultHrefFor } from "@/lib/vault";

import { BatchImport } from "./BatchImport";
import styles from "./DocumentsTab.module.css";
import { type FileImportRow, useImportQueue } from "./ImportQueueProvider";
import { ImportRow } from "./ImportRow";
import { ReadingList } from "./ReadingList";

const SKELETON_ROWS = ["a", "b", "c"] as const;

interface DocumentsTabProps {
  subjectCode: string;
  /** `?doc=&page=` của trang môn: có `doc` thì mở màn xem lại tài liệu đó. */
  location: SubjectDocumentLocation;
  hrefFor: VaultHrefFor;
  /** Xem lại xong một tài liệu: trang môn chuyển sang tab Khái niệm. */
  onFinished: () => void;
}

/**
 * Tab Tài liệu của trang môn (design-handoff/README.md màn 4–6): danh sách tài liệu (kèm hàng đợi gửi và màn "Đang
 * đọc"), màn "Thêm hàng loạt", và màn "Xem lại" khi địa chỉ có `?doc=`.
 */
export function DocumentsTab({ subjectCode, location, hrefFor, onFinished }: DocumentsTabProps) {
  if (location.doc !== null) {
    return (
      <PageReview
        key={location.doc}
        documentId={location.doc}
        pageNumber={location.page}
        hrefFor={hrefFor}
        expectedSubjectCode={subjectCode}
        onFinished={onFinished}
      />
    );
  }
  return <DocumentsHome subjectCode={subjectCode} hrefFor={hrefFor} />;
}

/** Cách sửa khi không mở được danh sách tài liệu, theo loại lỗi (mã kỹ thuật nằm trong "Chi tiết kỹ thuật"). */
function listErrorFix(error: UiError): string {
  if (error.kind === "network" || error.kind === "server") {
    return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.";
  }
  if (error.kind === "busy") return "Thư mục dữ liệu của Holocron đang bận. Đợi vài giây rồi bấm Thử lại.";
  return "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.";
}

function DocumentsLoading() {
  return (
    <div className={styles.loading} aria-busy="true">
      <ul className={styles.skeletonList} aria-hidden="true">
        {SKELETON_ROWS.map((key) => (
          <li key={key} className={styles.skeletonRow}>
            <Skeleton width={28} height={28} circle />
            <Skeleton width="60%" height={18} />
            <Skeleton width={90} height={18} />
          </li>
        ))}
      </ul>
      <output className={styles.loadingText}>
        <HolocronMark size={22} spinning />
        Đang mở danh sách tài liệu…
      </output>
    </div>
  );
}

type Mode = "list" | "import";

interface SendQueueProps {
  subjectCode: string;
  rows: readonly FileImportRow[];
}

/**
 * Phần "Gửi file": các file đã bấm đọc của môn và kết quả gửi từng file (có "Thử lại"). Hiện cả khi danh sách tài liệu
 * đang tải hay lỗi. Một vùng `aria-live` chung đọc tiến độ và kết quả MỚI (không đọc lại kết quả cũ khi quay lại trang).
 */
function SendQueue({ subjectCode, rows }: SendQueueProps) {
  const queue = useImportQueue();
  const seen = useRef<ReadonlyMap<string, ImportRowStatus> | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    const previous = seen.current;
    seen.current = new Map(rows.map((row) => [row.id, row.status]));
    if (previous === null) return;
    const fresh = newlySettled(previous, rows);
    if (fresh.length > 0) setAnnouncement(fresh.map(settledAnnouncement).join(" "));
  }, [rows]);

  if (rows.length === 0) return null;
  const progress = queueProgress(rows);
  const hasAccepted = rows.some((row) => row.status === "created" || row.status === "existing");

  return (
    <section className={styles.queue} aria-labelledby="documents-queue-title">
      <div className={styles.queueHead}>
        <h2 id="documents-queue-title" className={styles.queueTitle}>
          Gửi file
        </h2>
        <output className={styles.queueCount} aria-live="polite">
          Đã gửi {progress.settled}/{progress.total} file.{announcement === "" ? "" : ` ${announcement}`}
        </output>
      </div>
      <ul className={styles.queueList}>
        {rows.map((row) => (
          <ImportRow key={row.id} row={row} onRemove={() => queue.remove(row.id)} onRetry={() => queue.retry(row.id)} />
        ))}
      </ul>
      {hasAccepted && (
        <div>
          <Button variant="secondary" onClick={() => queue.clearSettled(subjectCode)}>
            Ẩn các file đã nhận
          </Button>
        </div>
      )}
    </section>
  );
}

function DocumentsHome({ subjectCode, hrefFor }: { subjectCode: string; hrefFor: VaultHrefFor }) {
  const { runtime } = useShell();
  const queue = useImportQueue();
  const { state, pollError, reload } = useDocumentsSnapshot(subjectCode);

  const subjectRows = useMemo(() => rowsOfSubject(queue.rows, subjectCode), [queue.rows, subjectCode]);
  const staged = useMemo(() => subjectRows.filter(isStaged), [subjectRows]);
  const sent = useMemo(() => subjectRows.filter((row) => !isStaged(row)), [subjectRows]);
  const [mode, setMode] = useState<Mode>(() => (staged.length > 0 ? "import" : "list"));

  // Backend vừa nhận thêm file của môn này: đọc lại danh sách để thấy tài liệu mới và tiến độ đọc.
  const completed = queue.completions[subjectCode] ?? 0;
  const seenCompleted = useRef(completed);
  useEffect(() => {
    if (seenCompleted.current === completed) return;
    seenCompleted.current = completed;
    reload();
  }, [completed, reload]);

  // Tài liệu đã thấy đang đọc trong lúc màn mở (và tài liệu vừa gửi xong): đọc xong vẫn giữ dòng với dấu tích.
  const documents = state.kind === "ready" ? state.snapshot.documents : null;
  const [watched, setWatched] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (documents === null) return;
    const processing = documents.filter((item) => item.status === "processing").map((item) => item.docId);
    setWatched((previous) =>
      processing.every((id) => previous.has(id)) ? previous : new Set([...previous, ...processing]),
    );
  }, [documents]);
  const watchedAll = useMemo(() => {
    const created = sent.filter((row) => row.status === "created" && row.documentId !== null);
    if (created.length === 0) return watched;
    const all = new Set(watched);
    for (const row of created) if (row.documentId !== null) all.add(row.documentId);
    return all;
  }, [watched, sent]);
  const reading = useMemo(
    () => (documents === null ? [] : readingRows(documents, watchedAll)),
    [documents, watchedAll],
  );

  if (mode === "import") {
    return (
      <div className={styles.root}>
        <CapabilityNotice runtime={freshRuntime(runtime)} name="ocr" />
        <BatchImport
          subjectCode={subjectCode}
          rows={staged}
          onBack={() => setMode("list")}
          onStarted={() => setMode("list")}
        />
      </div>
    );
  }

  if (state.kind === "loading") {
    return (
      <div className={styles.root}>
        <SendQueue subjectCode={subjectCode} rows={sent} />
        <DocumentsLoading />
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className={styles.root}>
        <SendQueue subjectCode={subjectCode} rows={sent} />
        <ErrorState
          title="Không mở được danh sách tài liệu"
          fix={listErrorFix(state.error)}
          onRetry={reload}
          technical={state.error.diagnostic}
        />
      </div>
    );
  }

  const snapshot = state.snapshot;
  if (snapshot.documents.length === 0 && sent.length === 0) {
    return (
      <div className={styles.root}>
        <CapabilityNotice runtime={freshRuntime(runtime)} name="ocr" />
        <EmptyState
          title="Chưa có tài liệu"
          description="Thêm file PDF bài giảng, Holocron đọc và giữ lại số trang của từng đoạn."
          action={{ label: "Thêm tài liệu", onClick: () => setMode("import") }}
        />
      </div>
    );
  }

  return (
    <div className={styles.root}>
      <div className={styles.head}>
        <h2 className={styles.title}>Tài liệu của môn</h2>
        <Button variant="primary" onClick={() => setMode("import")}>
          Thêm tài liệu
        </Button>
      </div>
      {staged.length > 0 && (
        <p className={styles.note}>
          Còn {staged.length} file đang chờ bạn kiểm lại trước khi đọc. Bấm “Thêm tài liệu” để tiếp tục.
        </p>
      )}
      <CapabilityNotice runtime={freshRuntime(runtime)} name="ocr" />

      {pollError !== null && (
        <ErrorState
          title="Đã dừng cập nhật tiến độ đọc"
          fix="Holocron không trả lời nhiều lần liền nên tiến độ trên màn có thể đã cũ. Bấm Thử lại để cập nhật tiếp."
          onRetry={reload}
          technical={pollError.diagnostic}
        />
      )}

      <SendQueue subjectCode={subjectCode} rows={sent} />

      <ReadingList rows={reading} reviewHref={(docId) => hrefFor({ subject: subjectCode, doc: docId, page: null })} />

      {snapshot.documents.length > 0 && <DocumentList snapshot={snapshot} onChanged={reload} hrefFor={hrefFor} />}
    </div>
  );
}
