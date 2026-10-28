"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { TesseractHelp } from "@/components/common/TesseractHelp";
import { ReviewBanner, ReviewCelebration } from "@/components/documents/ReadingList";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import {
  buildLineEdits,
  CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI,
  CONFIRM_PAGE_BLOCK_MESSAGE_VI,
  checkLineText,
  confirmDocumentBlock,
  confirmPageBlock,
  createPendingPagePoller,
  createSingleFlight,
  crossCheckLabel,
  DOCUMENT_STATUS_LABEL_VI,
  EDIT_BLOCK_MESSAGE_VI,
  engineLabel,
  lineEditBlock,
  lineHighlight,
  lineSaveDecision,
  mismatchesByLine,
  PAGE_STATUS_LABEL_VI,
  pageWarnings,
  sourceKindLabel,
  tierLabel,
} from "@/lib/documents";
import type { DocumentStatusView, DocumentView, LineView, PageStatusView, PageView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import type { Poller } from "@/lib/poll";
import { armsReview, classifyReviewFocus, lineToEdit, reviewKeyAction } from "@/lib/review-keys";
import {
  awaitingSettle,
  nextAfterConfirm,
  queueNeighbors,
  reviewOutcome,
  reviewProgress,
  reviewQueue,
} from "@/lib/review-queue";
import { type VaultHrefFor, vaultHref } from "@/lib/vault";

import { DeleteDialog } from "./DocumentList";
import styles from "./Vault.module.css";

type Loadable<T> = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; value: T };

const PAGE_TONE: Record<PageStatusView, "info" | "warning" | "success" | "danger" | "neutral"> = {
  pending: "info",
  ok: "success",
  needs_review: "warning",
  failed: "danger",
  unknown: "neutral",
};

/** Số backend trả, hiện nguyên giá trị (không quy đổi, không so ngưỡng); `null` thành gạch ngang. */
function rawNumber(value: number | null): string {
  return value === null ? "—" : String(value);
}

interface PageReviewProps {
  documentId: string;
  /** Trang đang xem; `null` ⇒ mở trang cần xem lại đầu tiên (không có thì trang đầu). */
  pageNumber: number | null;
  /**
   * Dựng địa chỉ trang khác, danh sách tài liệu của môn và nơi quay lại; mặc định `vaultHref` (`/vault?…`). Trang môn
   * truyền hàm giữ mọi liên kết trong phần Tài liệu của `/subject`.
   */
  hrefFor?: VaultHrefFor;
  /** Môn của trang đang mở (`/subject?code=`): tài liệu thuộc môn khác thì không hiện dưới tiêu đề môn này. */
  expectedSubjectCode?: string;
  /**
   * Xem lại xong (tài liệu vừa chuyển sang `confirmed` trong lúc màn mở): trang môn sang tab Khái niệm. Không truyền
   * (màn `/vault`) thì chỉ hiện lời chúc mừng.
   */
  onFinished?: () => void;
}

/**
 * Xem lại một trang (`/vault?doc=…&page=…` hoặc phần Tài liệu của `/subject`; §5.7, §6.5; design-handoff màn 6):
 * banner "Có N trang cần xem lại" (hàng đợi tính từ `pages[]` của tài liệu), danh sách trang bên trái, văn bản theo dòng
 * bên phải; phím Enter/E/←/→ trong vùng xem lại (`lib/review-keys.ts`). Xác nhận xong một trang cần xem thì mở trang
 * cần xem kế tiếp; tài liệu chuyển sang `confirmed` thì chúc mừng rồi sang tab Khái niệm. Dòng có cờ conf thấp hoặc
 * token lệch được tô theo dữ liệu backend; sửa dòng gửi `expected_version`, 409 lệch phiên bản thì có nút tải lại bản
 * mới nhất; xác nhận từng trang (`ok`, `needs_review`) và xác nhận cả tài liệu (chỉ trang `ok`).
 * Chưa có ảnh trang vì chưa có cách lấy file gốc (kế hoạch UI M12).
 */
export function PageReview({
  documentId,
  pageNumber,
  hrefFor = vaultHref,
  expectedSubjectCode,
  onFinished,
}: PageReviewProps) {
  const router = useRouter();
  const [document, setDocument] = useState<Loadable<DocumentView>>({ kind: "loading" });
  // Trạng thái của tài liệu lần đầu đọc được: chỉ chúc mừng khi tài liệu chuyển sang `confirmed` trong lúc màn mở.
  const [firstStatus, setFirstStatus] = useState<DocumentStatusView | null>(null);
  const [page, setPage] = useState<Loadable<PageView>>({ kind: "loading" });
  const [actionError, setActionError] = useState<UiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pollError, setPollError] = useState<UiError | null>(null);
  const pagePoller = useRef<Poller | null>(null);
  const docCalls = useRef(createLatestCounter());
  const pageCalls = useRef(createLatestCounter());
  // Cờ đổi ngay (không chờ render) để Enter rồi bấm "Lưu" liền tay chỉ gửi một thao tác ghi.
  const writes = useRef(createSingleFlight());

  const loadDocument = useCallback(() => {
    const ticket = docCalls.current.begin();
    api
      .getDocument(documentId)
      .then((value) => {
        if (!docCalls.current.isLatest(ticket)) return;
        setDocument({ kind: "ready", value });
        setFirstStatus((previous) => previous ?? value.status);
      })
      .catch((error: unknown) => {
        if (docCalls.current.isLatest(ticket)) setDocument({ kind: "error", error: toUiError(error) });
      });
  }, [documentId]);

  const loadPage = useCallback(() => {
    if (pageNumber === null) return;
    const ticket = pageCalls.current.begin();
    setPage({ kind: "loading" });
    api
      .getDocumentPage(documentId, pageNumber)
      .then((value) => {
        if (pageCalls.current.isLatest(ticket)) setPage({ kind: "ready", value });
      })
      .catch((error: unknown) => {
        if (pageCalls.current.isLatest(ticket)) setPage({ kind: "error", error: toUiError(error) });
      });
  }, [documentId, pageNumber]);

  useEffect(() => {
    loadDocument();
    const counter = docCalls.current;
    return () => counter.invalidate();
  }, [loadDocument]);

  useEffect(() => {
    setActionError(null);
    setNotice(null);
    loadPage();
    const counter = pageCalls.current;
    return () => counter.invalidate();
  }, [loadPage]);

  const pendingPage = page.kind === "ready" && page.value.status === "pending";

  // Trang đang đọc: tự hỏi lại 1–2 s một lần (§6.5). Dừng khi trang rời `pending` (đổi giá trị này), đổi trang hay tài
  // liệu (`loadPage` đặt lại về đang tải) hoặc rời màn.
  useEffect(() => {
    if (!pendingPage || pageNumber === null) return;
    setPollError(null);
    const poller = createPendingPagePoller({
      load: () => api.getDocumentPage(documentId, pageNumber),
      onValue: (value) => {
        pageCalls.current.invalidate();
        setPage({ kind: "ready", value });
        if (value.status !== "pending") loadDocument();
      },
      onError: (error, gaveUp) => {
        if (gaveUp) setPollError(toUiError(error));
      },
    });
    pagePoller.current = poller;
    poller.start();
    return () => {
      poller.stop();
      pagePoller.current = null;
    };
  }, [pendingPage, documentId, pageNumber, loadDocument]);

  /** Nút thử lại sau khi poller bỏ cuộc vì lỗi liên tiếp: chạy lại đúng poller của trang đang xem. */
  const retryPoll = useCallback(() => {
    setPollError(null);
    pagePoller.current?.start();
  }, []);

  /** Đọc lại bản mới nhất của trang và tài liệu (nút "Tải lại bản mới nhất"); không gửi lại thao tác ghi. */
  const reloadAll = useCallback(() => {
    setActionError(null);
    loadDocument();
    loadPage();
  }, [loadDocument, loadPage]);

  /** Chạy một thao tác ghi; thành công thì áp trang trả về và đọc lại tài liệu để cập nhật danh sách trang. */
  const runWrite = useCallback(
    async (write: () => Promise<PageView>, done: string): Promise<boolean> => {
      if (!writes.current.tryEnter()) return false;
      setBusy(true);
      setActionError(null);
      setNotice(null);
      try {
        const updated = await write();
        pageCalls.current.invalidate();
        setPage({ kind: "ready", value: updated });
        setNotice(done);
        loadDocument();
        return true;
      } catch (caught) {
        setActionError(toUiError(caught));
        return false;
      } finally {
        writes.current.leave();
        setBusy(false);
      }
    },
    [loadDocument],
  );

  const confirmAll = useCallback(async () => {
    if (!writes.current.tryEnter()) return;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const updated = await api.confirmDocument(documentId);
      docCalls.current.invalidate();
      setDocument({ kind: "ready", value: updated });
      setFirstStatus((previous) => previous ?? updated.status);
      setNotice("Đã xác nhận các trang đọc tốt. Trang cần xem kỹ vẫn chờ bạn xác nhận từng trang.");
      loadPage();
    } catch (caught) {
      setActionError(toUiError(caught));
    } finally {
      writes.current.leave();
      setBusy(false);
    }
  }, [documentId, loadPage]);

  const readyDocument = document.kind === "ready" ? document.value : null;
  const wrongSubject =
    readyDocument !== null && expectedSubjectCode !== undefined && readyDocument.subjectCode !== expectedSubjectCode;

  // Mở tài liệu không kèm số trang: chuyển tới trang cần xem lại đầu tiên (không có thì trang đầu tiên).
  useEffect(() => {
    if (pageNumber !== null || readyDocument === null || wrongSubject) return;
    const first = reviewQueue(readyDocument)[0] ?? readyDocument.pages[0]?.pageNumber ?? 1;
    router.replace(hrefFor({ subject: readyDocument.subjectCode, doc: readyDocument.docId, page: first }));
  }, [pageNumber, readyDocument, wrongSubject, router, hrefFor]);

  if (document.kind === "loading") return <Loading label="Đang tải tài liệu…" />;
  if (document.kind === "error") {
    return (
      <div className={styles.page}>
        <ErrorNotice error={document.error} onRetry={loadDocument} onReload={loadDocument} />
        <p>
          <Link href={hrefFor({})}>Quay lại danh sách</Link>
        </p>
      </div>
    );
  }
  const doc = document.value;
  if (wrongSubject) {
    return (
      <div className={styles.page}>
        <p role="alert">
          Tài liệu này thuộc môn {doc.subjectCode}, không thuộc môn {expectedSubjectCode}. Mở lại từ danh sách tài liệu
          của môn để xem đúng tài liệu.
        </p>
        <p>
          <Link href={hrefFor({})}>Quay lại danh sách</Link>
        </p>
      </div>
    );
  }
  const docBlock = confirmDocumentBlock(doc);
  const subjectHref = hrefFor({ subject: doc.subjectCode });
  const queue = reviewQueue(doc);
  const outcome = reviewOutcome(doc);
  const celebrating = outcome.kind === "done" && firstStatus !== null && firstStatus !== "confirmed";
  const neighbors = pageNumber === null ? { previous: null, next: null } : queueNeighbors(queue, pageNumber);
  const pageHref = (target: number) => hrefFor({ subject: doc.subjectCode, doc: doc.docId, page: target });

  if (celebrating) {
    return (
      <div className={styles.page}>
        <p>
          <Link href={subjectHref}>← Tài liệu của môn {doc.subjectCode}</Link>
        </p>
        <ReviewCelebration title={doc.title} onContinue={onFinished} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <section className={styles.panel} aria-labelledby="review-doc-title">
        <p>
          <Link href={subjectHref}>← Tài liệu của môn {doc.subjectCode}</Link>
        </p>
        <div className={styles.panelHeader}>
          <h2 id="review-doc-title">{doc.title}</h2>
          <span className={styles.badge}>{DOCUMENT_STATUS_LABEL_VI[doc.status]}</span>
        </div>
        <p className={styles.meta}>
          <span>{sourceKindLabel(doc.sourceKind)}</span>
          <span>{doc.chapterCode === null ? "Backend chưa trả mã chương" : `Chương ${doc.chapterCode}`}</span>
          <span>{doc.pageCount} trang</span>
          <span className={styles.fileName}>{doc.filename}</span>
        </p>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.secondary}
            onClick={() => void confirmAll()}
            disabled={busy || docBlock !== null}
            aria-describedby="review-doc-confirm-hint"
          >
            Xác nhận các trang đọc tốt
          </button>
          <button type="button" className={styles.danger} onClick={() => setDeleting(true)} disabled={busy}>
            Xoá tài liệu
          </button>
        </div>
        <p id="review-doc-confirm-hint" className={styles.hint}>
          {docBlock === null
            ? "Chỉ xác nhận các trang không có cảnh báo; trang cần xem kỹ phải xác nhận từng trang."
            : CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI[docBlock]}
        </p>
        <ReviewBanner
          queueLength={queue.length}
          progress={reviewProgress(doc)}
          outcome={outcome}
          awaitingSettle={awaitingSettle(doc)}
        />
      </section>

      <div className={styles.review}>
        <nav aria-label="Các trang của tài liệu">
          <ol className={styles.pageNav}>
            {doc.pages.map((item) => (
              <li key={item.pageNumber}>
                <Link
                  className={styles.pageLink}
                  data-status={item.status}
                  href={hrefFor({ subject: doc.subjectCode, doc: doc.docId, page: item.pageNumber })}
                  aria-current={item.pageNumber === pageNumber ? "page" : undefined}
                >
                  <span>Trang {item.pageNumber}</span>
                  <span className={styles.pageLinkStatus}>
                    {item.confirmed ? "Đã xác nhận" : PAGE_STATUS_LABEL_VI[item.status]}
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        </nav>

        <div className={styles.reviewMain}>
          {notice !== null && <output className={styles.successNote}>{notice}</output>}
          {actionError !== null && <ErrorNotice error={actionError} onRetry={reloadAll} onReload={reloadAll} />}
          {page.kind === "loading" && (
            <Loading label={pageNumber === null ? "Đang mở trang cần xem lại…" : `Đang tải trang ${pageNumber}…`} />
          )}
          {page.kind === "error" && <ErrorNotice error={page.error} onRetry={loadPage} onReload={loadPage} />}
          {pendingPage && pollError !== null && (
            <div className={styles.form}>
              <p className={styles.warningNote}>
                Đã dừng tự cập nhật trang đang đọc vì không hỏi được backend nhiều lần liền.
              </p>
              <ErrorNotice error={pollError} onRetry={retryPoll} onReload={retryPoll} />
              {pollError.action === "none" && (
                <div className={styles.actions}>
                  <button type="button" className={styles.secondary} onClick={retryPoll}>
                    Thử lại
                  </button>
                </div>
              )}
            </div>
          )}
          {page.kind === "ready" && (
            <PagePanel
              key={`${page.value.pageNumber}:${page.value.version}`}
              page={page.value}
              busy={busy}
              hasPrevious={neighbors.previous !== null}
              hasNext={neighbors.next !== null}
              onStep={(direction) => {
                const target = direction === "previous" ? neighbors.previous : neighbors.next;
                if (target !== null) router.push(pageHref(target));
              }}
              onDelete={() => setDeleting(true)}
              onReload={reloadAll}
              onSaveLines={(edits, appended) =>
                runWrite(
                  () => api.patchPageLines(documentId, page.value.pageNumber, page.value.version, edits, appended),
                  "Đã lưu dòng vừa sửa.",
                )
              }
              onConfirm={async () => {
                const current = page.value.pageNumber;
                const ok = await runWrite(
                  () => api.confirmPage(documentId, current, page.value.version),
                  "Đã xác nhận trang.",
                );
                // Xác nhận xong một trang cần xem: mở trang cần xem kế tiếp.
                const target = ok && queue.includes(current) ? nextAfterConfirm(queue, current) : null;
                if (target !== null) router.push(pageHref(target));
                return ok;
              }}
            />
          )}
          {pageNumber !== null && <PageSteps doc={doc} pageNumber={pageNumber} hrefFor={hrefFor} />}
        </div>
      </div>

      <DeleteDialog
        open={deleting}
        title={doc.title}
        documentId={doc.docId}
        onClose={() => setDeleting(false)}
        onDeleted={() => {
          setDeleting(false);
          router.push(subjectHref);
        }}
        onReload={() => {
          setDeleting(false);
          reloadAll();
        }}
      />
    </div>
  );
}

function PageSteps({ doc, pageNumber, hrefFor }: { doc: DocumentView; pageNumber: number; hrefFor: VaultHrefFor }) {
  const index = doc.pages.findIndex((item) => item.pageNumber === pageNumber);
  const previous = index > 0 ? doc.pages[index - 1] : undefined;
  const next = index >= 0 ? doc.pages[index + 1] : undefined;
  return (
    <nav className={styles.pager} aria-label="Chuyển trang">
      {previous !== undefined && (
        <Link href={hrefFor({ subject: doc.subjectCode, doc: doc.docId, page: previous.pageNumber })}>
          ← Trang {previous.pageNumber}
        </Link>
      )}
      {next !== undefined && (
        <Link href={hrefFor({ subject: doc.subjectCode, doc: doc.docId, page: next.pageNumber })}>
          Trang {next.pageNumber} →
        </Link>
      )}
    </nav>
  );
}

interface PagePanelProps {
  page: PageView;
  busy: boolean;
  /** Có trang cần xem lại ở phía trước/sau (phím ←/→). */
  hasPrevious: boolean;
  hasNext: boolean;
  onStep: (direction: "previous" | "next") => void;
  onSaveLines: (edits: ReturnType<typeof buildLineEdits>, appended: string[]) => Promise<boolean>;
  onConfirm: () => Promise<boolean>;
  onDelete: () => void;
  onReload: () => void;
}

/**
 * Vùng xem lại một trang. Phím tắt chỉ chạy khi focus ở trong vùng: Enter xác nhận trang, E sửa dòng nghi ngờ đầu tiên,
 * ←/→ sang trang cần xem trước/sau (`lib/review-keys.ts`). Trang mới hiện (tự chuyển sau khi xác nhận, mở từ danh sách,
 * vẽ lại sau khi lưu dòng) mà focus đang mất thì focus đặt vào tiêu đề trang, KHÔNG mở khoá Enter: người dùng phải bấm
 * chuột, dùng phím khác hay chuyển focus vào trang trước, và Enter chỉ có tác dụng sau `REVIEW_ENTER_GUARD_MS` (I5).
 */
function PagePanel({
  page,
  busy,
  hasPrevious,
  hasNext,
  onStep,
  onSaveLines,
  onConfirm,
  onDelete,
  onReload,
}: PagePanelProps) {
  const ids = useId();
  const { overlayOpen } = useShell();
  const region = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  // Mốc lúc trang hiện ra và việc người dùng đã chủ động tương tác với trang này chưa (không gây vẽ lại).
  const shownAt = useRef(0);
  const armed = useRef(false);
  const editBlock = lineEditBlock(page);
  const confirmBlock = confirmPageBlock(page);
  const mismatches = mismatchesByLine(page.crossCheck.mismatches);
  const warnings = pageWarnings({ ...page, failureReason: page.failureReason });
  const [editing, setEditing] = useState<number | null>(null);
  const [appendDraft, setAppendDraft] = useState(false);

  useEffect(() => {
    shownAt.current = performance.now();
    armed.current = false;
    const active = window.document.activeElement;
    if (active === null || active === window.document.body) heading.current?.focus();
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLElement>): void {
    const wasArmed = armed.current;
    if (armsReview({ type: "keydown", key: event.key, repeat: event.repeat })) armed.current = true;
    const target = event.target instanceof HTMLElement ? event.target : null;
    const root = region.current;
    const focus = classifyReviewFocus({
      insideRegion: target !== null && (root?.contains(target) ?? false),
      isRegion: target !== null && target === root,
      tagName: target?.tagName ?? "",
      isContentEditable: target?.isContentEditable ?? false,
      role: target?.getAttribute("role") ?? null,
    });
    const action = reviewKeyAction({
      key: event.key,
      repeat: event.repeat,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      focus,
      overlayOpen,
      busy,
      hasDraft: editing !== null || appendDraft,
      confirmBlock,
      hasSkippedLines: page.hasSkippedLines,
      canEdit: editBlock === null && page.lines.length > 0,
      hasPrevious,
      hasNext,
      armed: wasArmed,
      msSinceShown: performance.now() - shownAt.current,
    });
    if (action === null) return;
    event.preventDefault();
    if (action.type === "confirm") {
      void onConfirm();
    } else if (action.type === "edit") {
      const index = lineToEdit(
        page.lines.map((line) => {
          const highlight = lineHighlight(line, mismatches);
          return { lineIndex: line.lineIndex, flagged: highlight.lowConf || highlight.mismatch };
        }),
      );
      if (index !== null) setEditing(index);
    } else {
      onStep(action.type);
    }
  }

  return (
    <section
      ref={region}
      className={styles.panel}
      aria-labelledby={`${ids}-title`}
      aria-describedby={`${ids}-keys`}
      onKeyDown={onKeyDown}
      onPointerDown={() => {
        if (armsReview({ type: "pointerdown" })) armed.current = true;
      }}
      onFocus={(event) => {
        if (armsReview({ type: "focusin", fromElement: event.relatedTarget instanceof Element })) armed.current = true;
      }}
    >
      <div className={styles.panelHeader}>
        <h2 id={`${ids}-title`} ref={heading} tabIndex={-1}>
          Trang {page.pageNumber}
        </h2>
        <span className={styles.badge} data-tone={PAGE_TONE[page.status]}>
          {page.confirmed ? `${PAGE_STATUS_LABEL_VI[page.status]} · đã xác nhận` : PAGE_STATUS_LABEL_VI[page.status]}
        </span>
      </div>
      <p id={`${ids}-keys`} className={styles.hint}>
        Phím tắt khi đang ở trang này: E sửa dòng nghi ngờ đầu tiên · ← → trang cần xem lại trước, sau · Enter xác nhận
        trang, sau khi bạn đã bấm vào trang hoặc dùng một phím khác.
      </p>

      <dl className={styles.facts}>
        <dt>Cách đọc</dt>
        <dd>{tierLabel(page.tierUsed) ?? "—"}</dd>
        <dt>Đối chiếu hai lượt đọc</dt>
        <dd>{crossCheckLabel(page.crossCheck.status)}</dd>
        <dt>Điểm lớp chữ</dt>
        <dd>{rawNumber(page.textScore)}</dd>
        <dt>Điểm OCR</dt>
        <dd>{rawNumber(page.ocrScore)}</dd>
        <dt>Tỉ lệ số dòng hai lượt</dt>
        <dd>{rawNumber(page.coverageRatio)}</dd>
        <dt>Tỉ lệ mực ngoài vùng chữ</dt>
        <dd>{rawNumber(page.inkOutsideTextRatio)}</dd>
        <dt>Trang có</dt>
        <dd>
          {[page.hasFigure ? "hình" : null, page.hasTable ? "bảng" : null, page.hasFormula ? "công thức" : null]
            .filter((item) => item !== null)
            .join(", ") || "chỉ có chữ"}
        </dd>
      </dl>
      <p className={styles.hint}>Các số trên là giá trị backend tính, hiện nguyên văn để tham khảo.</p>

      {warnings.length > 0 && (
        <ul className={styles.warningList} aria-label="Cảnh báo của trang">
          {warnings.map((warning) => (
            <li key={warning.key} className={styles.warning} data-tone={warning.tone}>
              {warning.label}
            </li>
          ))}
        </ul>
      )}

      {page.status === "failed" && (
        <div className={styles.form}>
          <p className={styles.warningNote}>{EDIT_BLOCK_MESSAGE_VI.failed}</p>
          {page.failureReason === "OCR_ENGINE_MISSING" && <TesseractHelp />}
          <div className={styles.actions}>
            <button type="button" className={styles.danger} onClick={onDelete} disabled={busy}>
              Xoá tài liệu để nhập lại
            </button>
          </div>
        </div>
      )}
      {page.status === "pending" && (
        <div className={styles.actions}>
          <p className={styles.note}>
            {EDIT_BLOCK_MESSAGE_VI.pending} Trang tự cập nhật vài giây một lần trong lúc đọc.
          </p>
          <button type="button" className={styles.secondary} onClick={onReload}>
            Tải lại
          </button>
        </div>
      )}
      {page.status === "unknown" && <p className={styles.warningNote}>{EDIT_BLOCK_MESSAGE_VI.unknown}</p>}
      {page.hasSkippedLines && (
        <p className={styles.warningNote}>
          Có dòng backend trả về sai dạng nên không hiển thị. Đừng xác nhận trang này trước khi tải lại và kiểm tra.
        </p>
      )}

      {page.crossCheck.mismatches.length > 0 && (
        <table className={styles.mismatchTable}>
          <caption className={styles.hint}>Số hoặc mã hai lượt đọc ra khác nhau</caption>
          <thead>
            <tr>
              <th scope="col">Dòng</th>
              <th scope="col">Lượt đọc chính</th>
              <th scope="col">Lượt đọc thứ hai</th>
            </tr>
          </thead>
          <tbody>
            {page.crossCheck.mismatches.map((mismatch) => (
              <tr key={`${mismatch.lineIndex}:${mismatch.primaryValue}:${mismatch.secondaryValue ?? ""}`}>
                <td>{mismatch.lineIndex}</td>
                <td>{mismatch.primaryValue}</td>
                <td>{mismatch.secondaryValue ?? "(không đọc ra)"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {page.lines.length > 0 && (
        <ol className={styles.lines} aria-label="Các dòng của trang">
          {page.lines.map((line) => (
            <LineRow
              key={line.lineIndex}
              line={line}
              highlight={lineHighlight(line, mismatches)}
              editable={editBlock === null && !busy}
              busy={busy}
              editing={editing === line.lineIndex}
              onStartEdit={() => setEditing(line.lineIndex)}
              onCancel={() => setEditing(null)}
              onSave={async (text) => {
                const edits = buildLineEdits(page.lines, new Map([[line.lineIndex, text]]));
                if (edits.length === 0) {
                  setEditing(null);
                  return;
                }
                if (await onSaveLines(edits, [])) setEditing(null);
              }}
            />
          ))}
        </ol>
      )}
      {page.lines.length === 0 && page.status !== "failed" && page.status !== "pending" && (
        <p className={styles.muted}>Trang không có dòng chữ nào.</p>
      )}
      {editBlock === "confirmed" && <p className={styles.hint}>{EDIT_BLOCK_MESSAGE_VI.confirmed}</p>}

      {editBlock === null && (
        <AppendLine busy={busy} onAppend={(text) => onSaveLines([], [text])} onDraftChange={setAppendDraft} />
      )}

      {page.status !== "failed" && page.status !== "pending" && (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.primary}
            onClick={() => void onConfirm()}
            disabled={busy || confirmBlock !== null || editing !== null || page.hasSkippedLines}
            aria-describedby={`${ids}-confirm-hint`}
          >
            {page.status === "needs_review" ? "Tôi đã xem lại, xác nhận trang" : "Xác nhận trang"}
          </button>
          <span id={`${ids}-confirm-hint`} className={styles.hint}>
            {editing !== null
              ? "Lưu hoặc huỷ dòng đang sửa trước khi xác nhận."
              : confirmBlock === null
                ? "Sau khi xác nhận, văn bản của trang không sửa được nữa."
                : CONFIRM_PAGE_BLOCK_MESSAGE_VI[confirmBlock]}
          </span>
        </div>
      )}
    </section>
  );
}

interface LineRowProps {
  line: LineView;
  highlight: ReturnType<typeof lineHighlight>;
  editable: boolean;
  /** Đang có thao tác ghi: Enter và nút "Lưu" không gửi thêm. */
  busy: boolean;
  editing: boolean;
  onStartEdit: () => void;
  onCancel: () => void;
  onSave: (text: string) => Promise<void>;
}

/** Một dòng: chữ backend trả, cờ thành nhãn tiếng Việt; sửa tại chỗ (`Enter` lưu, `Esc` huỷ). */
function LineRow({ line, highlight, editable, busy, editing, onStartEdit, onCancel, onSave }: LineRowProps) {
  const [draft, setDraft] = useState(line.text);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    if (editing) {
      setDraft(line.text);
      setError(null);
      input.current?.focus();
    } else if (wasEditing.current) {
      editButton.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing, line.text]);

  function save(): void {
    const decision = lineSaveDecision(draft, busy);
    if (decision.kind === "blocked") return;
    setError(decision.kind === "invalid" ? decision.message : null);
    if (decision.kind === "save") void onSave(draft);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter") {
      event.preventDefault();
      save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCancel();
    }
  }

  const details = [...highlight.labels];
  if (line.editedByHuman) details.push("đã sửa tay");
  if (line.engine === "human") details.push(engineLabel(line.engine));

  return (
    <li className={styles.line} data-low-conf={highlight.lowConf} data-mismatch={highlight.mismatch}>
      <span className={styles.lineIndex} aria-hidden="true">
        {line.lineIndex}
      </span>
      <div className={styles.lineText}>
        {editing ? (
          <>
            <input
              ref={input}
              className={styles.lineEditor}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              aria-label={`Sửa dòng ${line.lineIndex}`}
              aria-invalid={error !== null}
              spellCheck={false}
            />
            {error !== null && <span className={styles.fieldError}>{error}</span>}
            <span className={styles.hint}>Enter để lưu, Esc để huỷ. Để trống nghĩa là bỏ dòng này khỏi văn bản.</span>
          </>
        ) : (
          <span className={line.text.trim() === "" ? styles.lineEmpty : undefined}>
            {line.text.trim() === "" ? "(dòng trống)" : line.text}
          </span>
        )}
        {details.length > 0 && <span className={styles.lineFlags}>{details.join(" · ")}</span>}
      </div>
      <div className={styles.actions}>
        {editing ? (
          <>
            <button type="button" className={`${styles.primary} ${styles.small}`} onClick={save} disabled={busy}>
              {busy ? "Đang lưu…" : "Lưu"}
            </button>
            <button type="button" className={`${styles.secondary} ${styles.small}`} onClick={onCancel}>
              Huỷ
            </button>
          </>
        ) : (
          editable && (
            <button
              ref={editButton}
              type="button"
              className={`${styles.secondary} ${styles.small}`}
              onClick={onStartEdit}
              aria-label={`Sửa dòng ${line.lineIndex}`}
            >
              Sửa
            </button>
          )
        )}
      </div>
    </li>
  );
}

interface AppendLineProps {
  busy: boolean;
  onAppend: (text: string) => Promise<boolean>;
  /** Ô gõ thêm có chữ chưa lưu: Enter của vùng xem lại không xác nhận trang. */
  onDraftChange: (hasDraft: boolean) => void;
}

/** Gõ thêm một dòng vào cuối trang (`appended_lines`, §6.5), cho chữ mà bộ đọc bỏ sót. */
function AppendLine({ busy, onAppend, onDraftChange }: AppendLineProps) {
  const ids = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const hasDraft = text.trim() !== "";

  useEffect(() => {
    onDraftChange(hasDraft);
  }, [hasDraft, onDraftChange]);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (text.trim() === "") {
      setError("Nhập nội dung dòng cần thêm.");
      return;
    }
    const problem = checkLineText(text);
    setError(problem);
    if (problem !== null) return;
    if (await onAppend(text.normalize("NFC"))) setText("");
  }

  return (
    <form className={styles.actions} onSubmit={(event) => void submit(event)} noValidate>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Thêm dòng vào cuối trang</span>
        <input
          className={`${styles.input} ${styles.wideInput}`}
          value={text}
          onChange={(event) => setText(event.target.value)}
          aria-invalid={error !== null}
          aria-describedby={`${ids}-hint`}
          disabled={busy}
        />
        <span id={`${ids}-hint`} className={error === null ? styles.hint : styles.fieldError}>
          {error ?? "Dùng khi bộ đọc bỏ sót một dòng của trang."}
        </span>
      </label>
      <button type="submit" className={styles.secondary} disabled={busy}>
        Thêm dòng
      </button>
    </form>
  );
}
