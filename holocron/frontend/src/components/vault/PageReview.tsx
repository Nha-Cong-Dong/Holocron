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
import {
  type ActiveOnShow,
  canConfirmNow,
  confirmButtonBlock,
  confirmClick,
  focusOnShow,
  nextReviewTarget,
  unsavedDrafts,
} from "@/lib/review-confirm";
import {
  bulkConfirmBlock,
  closeLineEdit,
  DRAFT_PAGE_LABEL_VI,
  draftFor,
  draftPagesNote,
  draftStatusMessage,
  lineEditState,
  lockedDraftNotice,
  NO_DRAFTS,
  type PageDraft,
  pagesWithDrafts,
  type ReviewDrafts,
  setAppendText,
  setLineEditText,
  startLineEdit,
} from "@/lib/review-drafts";
import { classifyReviewFocus, lineToEdit, reviewKeyAction } from "@/lib/review-keys";
import { awaitingSettle, queueNeighbors, reviewOutcome, reviewProgress, reviewQueue } from "@/lib/review-queue";
import {
  endSend,
  guardRemainingMs,
  msSinceShown,
  type ReviewSession,
  reviewPageKey,
  reviewSessionFor,
} from "@/lib/review-session";
import { type VaultHrefFor, vaultHref } from "@/lib/vault";

import { DeleteDialog } from "./DocumentList";
import pageStyles from "./PageReview.module.css";
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
 * bên phải; phím E/←/→ trong vùng xem lại (`lib/review-keys.ts`). Trang chỉ được xác nhận bằng nút (I5,
 * `lib/review-confirm.ts`); xác nhận xong thì trang ở lại, cạnh tiêu đề hiện nút "Sang trang cần xem kế tiếp" khi còn
 * trang cần xem khác (tính từ tài liệu đọc lại sau khi xác nhận). Tài liệu chuyển sang `confirmed` thì chúc mừng rồi
 * sang tab Khái niệm. Dòng có cờ conf thấp hoặc
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
  // Cờ đổi ngay (không chờ render) để hai thao tác ghi bấm liền tay (Enter ở ô sửa dòng rồi "Lưu") chỉ gửi một lần.
  const writes = useRef(createSingleFlight());
  // Trang vừa xác nhận thành công trên màn này, sau khi đã đọc lại tài liệu: hiện nút "Sang trang cần xem kế tiếp".
  const [confirmedHere, setConfirmedHere] = useState<number | null>(null);
  // Trang hiện lần trước, để biết trang mới hiện là đổi trang hay vẽ lại cùng trang (đặt focus, `focusOnShow`).
  const lastShownPage = useRef<number | null>(null);
  // Nháp chưa lưu theo số trang (`lib/review-drafts.ts`): giữ ở đây, không ở `PagePanel`, để vùng trang vẽ lại (lưu dòng
  // khác làm `version` tăng) hay đổi trang rồi quay lại không làm mất nháp (I5).
  const [drafts, setDrafts] = useState<ReviewDrafts>(NO_DRAFTS);

  /** Đọc lại tài liệu; trả bản vừa áp, `null` khi lỗi hoặc đã có lần đọc mới hơn. */
  const fetchDocument = useCallback((): Promise<DocumentView | null> => {
    const ticket = docCalls.current.begin();
    return api
      .getDocument(documentId)
      .then((value) => {
        if (!docCalls.current.isLatest(ticket)) return null;
        setDocument({ kind: "ready", value });
        setFirstStatus((previous) => previous ?? value.status);
        return value;
      })
      .catch((error: unknown) => {
        if (docCalls.current.isLatest(ticket)) setDocument({ kind: "error", error: toUiError(error) });
        return null;
      });
  }, [documentId]);

  const loadDocument = useCallback(() => {
    void fetchDocument();
  }, [fetchDocument]);

  const markShown = useCallback((shown: number): number | null => {
    const previous = lastShownPage.current;
    lastShownPage.current = shown;
    return previous;
  }, []);

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
    setConfirmedHere(null);
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

  /**
   * Chạy một thao tác ghi; thành công thì áp trang trả về và đọc lại tài liệu để cập nhật danh sách trang.
   * `onDocumentReloaded` chạy khi bản đọc lại đó đã áp (không chạy nếu đọc lỗi hoặc có lần đọc mới hơn).
   */
  const runWrite = useCallback(
    async (write: () => Promise<PageView>, done: string, onDocumentReloaded?: () => void): Promise<boolean> => {
      if (!writes.current.tryEnter()) return false;
      setBusy(true);
      setActionError(null);
      setNotice(null);
      try {
        const updated = await write();
        pageCalls.current.invalidate();
        setPage({ kind: "ready", value: updated });
        setNotice(done);
        void fetchDocument().then((fresh) => {
          if (fresh !== null) onDocumentReloaded?.();
        });
        return true;
      } catch (caught) {
        setActionError(toUiError(caught));
        return false;
      } finally {
        writes.current.leave();
        setBusy(false);
      }
    },
    [fetchDocument],
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
  const docBlock = bulkConfirmBlock(doc, drafts);
  // Trang còn nháp (mọi trang của tài liệu còn hiện nháp, kể cả trang đã khoá sửa, như điều kiện khoá nút): nhãn chữ
  // trong danh sách trang và câu liệt kê dưới dòng nhắc khi nút "Xác nhận các trang đọc tốt" khoá vì nháp.
  const draftPages = pagesWithDrafts(drafts, doc.pages);
  const draftPageSet = new Set(draftPages);
  const draftNote = docBlock === "unsaved_lines" ? draftPagesNote(draftPages) : null;
  const subjectHref = hrefFor({ subject: doc.subjectCode });
  const queue = reviewQueue(doc);
  const outcome = reviewOutcome(doc);
  const celebrating = outcome.kind === "done" && firstStatus !== null && firstStatus !== "confirmed";
  const neighbors = pageNumber === null ? { previous: null, next: null } : queueNeighbors(queue, pageNumber);
  const pageHref = (target: number) => hrefFor({ subject: doc.subjectCode, doc: doc.docId, page: target });
  // Đích tính từ `doc` hiện tại: `confirmedHere` chỉ được đặt sau khi bản đọc lại sau xác nhận đã áp.
  const nextTarget = pageNumber !== null && confirmedHere === pageNumber ? nextReviewTarget(doc, pageNumber) : null;

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
            onClick={() => {
              // Kiểm lại đúng điều kiện của `disabled` lúc bấm; còn nháp ở trang nào thì không gửi, không lưu hộ.
              if (busy || bulkConfirmBlock(doc, drafts) !== null) return;
              void confirmAll();
            }}
            disabled={busy || docBlock !== null}
            aria-describedby={
              draftNote === null ? "review-doc-confirm-hint" : "review-doc-confirm-hint review-doc-draft-pages"
            }
          >
            Xác nhận các trang đọc tốt
          </button>
          <button type="button" className={styles.danger} onClick={() => setDeleting(true)} disabled={busy}>
            Xoá tài liệu
          </button>
        </div>
        <p id="review-doc-confirm-hint" className={docBlock === "unsaved_lines" ? pageStyles.unsavedNote : styles.hint}>
          {docBlock === null
            ? "Chỉ xác nhận các trang không có cảnh báo; trang cần xem kỹ phải xác nhận từng trang."
            : docBlock === "unsaved_lines"
              ? CONFIRM_PAGE_BLOCK_MESSAGE_VI.unsaved_lines
              : CONFIRM_DOCUMENT_BLOCK_MESSAGE_VI[docBlock]}
        </p>
        {draftNote !== null && (
          <p id="review-doc-draft-pages" className={styles.hint}>
            {draftNote}
          </p>
        )}
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
                  {draftPageSet.has(item.pageNumber) && (
                    <span className={pageStyles.unsavedNote}>{DRAFT_PAGE_LABEL_VI}</span>
                  )}
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
              draft={draftFor(drafts, page.value.pageNumber)}
              onAppendTextChange={(text) =>
                setDrafts((previous) => setAppendText(previous, page.value.pageNumber, text))
              }
              onStartEdit={(lineIndex, text) =>
                setDrafts((previous) => startLineEdit(previous, page.value.pageNumber, lineIndex, text))
              }
              onEditTextChange={(text) =>
                setDrafts((previous) => setLineEditText(previous, page.value.pageNumber, text))
              }
              onCloseEdit={(lineIndex) =>
                setDrafts((previous) => closeLineEdit(previous, page.value.pageNumber, lineIndex))
              }
              busy={busy}
              hasPrevious={neighbors.previous !== null}
              hasNext={neighbors.next !== null}
              nextTarget={nextTarget}
              onGoNext={() => {
                if (nextTarget !== null) router.push(pageHref(nextTarget));
              }}
              onShown={markShown}
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
              onConfirm={() => {
                const current = page.value.pageNumber;
                // Không tự chuyển trang: trang ở lại; nút "Sang trang cần xem kế tiếp" hiện khi tài liệu đã đọc lại.
                return runWrite(
                  () => api.confirmPage(documentId, current, page.value.version),
                  "Đã xác nhận trang.",
                  () => setConfirmedHere(current),
                );
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
  /** Nháp chưa lưu của trang, do `PageReview` giữ theo số trang (không mất khi vùng này vẽ lại hay đổi trang). */
  draft: PageDraft;
  onAppendTextChange: (text: string) => void;
  /** Mở ô "Sửa" cho dòng `lineIndex`, chữ ban đầu là chữ hiện tại của dòng. */
  onStartEdit: (lineIndex: number, text: string) => void;
  onEditTextChange: (text: string) => void;
  /** Đóng ô "Sửa" của đúng dòng `lineIndex`. */
  onCloseEdit: (lineIndex: number) => void;
  busy: boolean;
  /** Có trang cần xem lại ở phía trước/sau (phím ←/→). */
  hasPrevious: boolean;
  hasNext: boolean;
  /** Đích của nút "Sang trang cần xem kế tiếp"; `null` thì không hiện nút. */
  nextTarget: number | null;
  onGoNext: () => void;
  /** Báo trang vừa hiện; trả trang hiện lần trước (để biết có phải đổi trang không). */
  onShown: (pageNumber: number) => number | null;
  onStep: (direction: "previous" | "next") => void;
  onSaveLines: (edits: ReturnType<typeof buildLineEdits>, appended: string[]) => Promise<boolean>;
  onConfirm: () => Promise<boolean>;
  onDelete: () => void;
  onReload: () => void;
}

/**
 * Vùng xem lại một trang. `tabIndex=-1`: bấm chuột vào nội dung trang đưa focus vào vùng; Tab đi tới các nút, ô nhập
 * trong vùng (`tabIndex=0` trên `section` bị rule a11y `noNoninteractiveTabindex` chặn). Phím tắt chỉ chạy khi focus ở
 * trong vùng: E sửa dòng nghi ngờ đầu tiên, ←/→ sang trang cần xem trước/sau (`lib/review-keys.ts`); không phím nào
 * xác nhận trang. Trang chỉ được xác nhận bằng nút, theo `canConfirmNow` (`lib/review-confirm.ts`): mỗi trang (theo
 * `pageNumber:version`) là một phiên (`lib/review-session.ts`), nút bỏ qua mọi kích hoạt (chuột, chạm, Enter/Space khi
 * nút đang focus) trong `CONFIRM_GUARD_MS` đầu, khoá khi đang gửi hoặc còn dòng chưa lưu (I5). Trang mới hiện thì focus
 * vào tiêu đề trang theo `focusOnShow`, không bao giờ vào nút xác nhận.
 */
function PagePanel({
  page,
  draft,
  onAppendTextChange,
  onStartEdit,
  onEditTextChange,
  onCloseEdit,
  busy,
  hasPrevious,
  hasNext,
  nextTarget,
  onGoNext,
  onShown,
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
  const confirmButton = useRef<HTMLButtonElement>(null);
  // Phiên xem trang: mốc lúc trang hiện ra và khoá đang gửi, đổi ngay khi bấm (không chờ vẽ lại).
  const pageKey = reviewPageKey(page.pageNumber, page.version);
  const session = useRef<ReviewSession | null>(null);
  const editBlock = lineEditBlock(page);
  const confirmBlock = confirmPageBlock(page);
  const mismatches = mismatchesByLine(page.crossCheck.mismatches);
  const warnings = pageWarnings({ ...page, failureReason: page.failureReason });
  // Ô sửa đối chiếu với bản trang đang hiện: trang bị khoá sửa, dòng đã đổi hoặc biến mất ở nơi khác thì ô đóng, hiện
  // câu báo tại chỗ; dòng đã mang đúng chữ đang gõ thì nháp đã khớp server và ô đóng. Xung đột vẫn là nháp chưa lưu
  // (khoá nút xác nhận) cho tới khi người dùng sửa lại hoặc bỏ chữ đã gõ.
  const editState = draft.editing === null ? null : lineEditState(draft.editing, page.lines, editBlock);
  const editing = editState?.kind === "open" ? (draft.editing?.lineIndex ?? null) : null;
  const matchedLine = editState?.kind === "matched" ? (draft.editing?.lineIndex ?? null) : null;
  const lockedNotice = lockedDraftNotice(draft, editBlock);
  const drafts = unsavedDrafts({ appendText: draft.appendText, editingLine: draft.editing?.lineIndex ?? null });
  // Vùng thông báo cố định (luôn có, rỗng khi không có gì): nội dung đổ vào SAU khi vùng đã có trong trang, kể cả lúc
  // vùng trang mount lại sau 409, để trình đọc màn hình đọc được. Không di chuyển focus khi báo xung đột.
  const statusMessage = draftStatusMessage(draft, editState, editBlock);
  const [announced, setAnnounced] = useState("");
  useEffect(() => {
    setAnnounced(statusMessage);
  }, [statusMessage]);

  // Nháp đã khớp server: đóng ô sửa của đúng dòng đó.
  useEffect(() => {
    if (matchedLine !== null) onCloseEdit(matchedLine);
  }, [matchedLine, onCloseEdit]);

  /** "Bỏ chữ đã gõ" ở câu báo cấp trang: nút biến mất, focus về tiêu đề trang (cùng đích của `focusOnShow`). */
  function discardAndFocusHeading(discard: () => void): void {
    discard();
    heading.current?.focus();
  }
  const [sending, setSending] = useState(false);
  // Số ms đã qua kể từ lúc trang hiện, đo khi hết `CONFIRM_GUARD_MS` để vẽ lại nút (chỉ cho `disabled`; `onClick` đo lại).
  const [shownElapsed, setShownElapsed] = useState<{ pageKey: string; ms: number } | null>(null);

  useEffect(() => {
    session.current = reviewSessionFor(session.current, pageKey, performance.now());
    let timer: number | undefined;
    const check = () => {
      const now = performance.now();
      const current = reviewSessionFor(session.current, pageKey, now);
      const remaining = guardRemainingMs(current, now);
      if (remaining === 0) setShownElapsed({ pageKey, ms: msSinceShown(current, now) });
      else timer = window.setTimeout(check, remaining);
    };
    check();
    return () => window.clearTimeout(timer);
  }, [pageKey]);

  useEffect(() => {
    const previous = onShown(page.pageNumber);
    const active = window.document.activeElement;
    const where: ActiveOnShow =
      active === null || active === window.document.body
        ? "none"
        : active === confirmButton.current
          ? "confirm"
          : "other";
    if (focusOnShow({ pageChanged: previous !== page.pageNumber, active: where }) === "heading") {
      heading.current?.focus();
    }
  }, [onShown, page.pageNumber]);

  const confirmInput = {
    pageBlock: confirmBlock,
    hasSkippedLines: page.hasSkippedLines,
    ...drafts,
  };
  const buttonBlock = confirmButtonBlock(confirmInput);
  const confirmEnabled = canConfirmNow({
    ...confirmInput,
    sending: sending || busy,
    msSinceShown: shownElapsed?.pageKey === pageKey ? shownElapsed.ms : 0,
  });

  /** Mọi cách kích hoạt nút (chuột, chạm, Enter/Space khi nút đang focus) đều qua đây: đo lại điều kiện lúc bấm. */
  function onConfirmClick(): void {
    const now = performance.now();
    const { accepted, session: next } = confirmClick(
      reviewSessionFor(session.current, pageKey, now),
      { ...confirmInput, busy },
      now,
    );
    session.current = next;
    if (!accepted) return;
    setSending(true);
    void onConfirm().finally(() => {
      if (session.current !== null) session.current = endSend(session.current);
      setSending(false);
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>): void {
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
      hasDraft: drafts.editing || drafts.appendDraft,
      canEdit: editBlock === null && page.lines.length > 0,
      hasPrevious,
      hasNext,
    });
    if (action === null) return;
    event.preventDefault();
    if (action.type === "edit") {
      const index = lineToEdit(
        page.lines.map((line) => {
          const highlight = lineHighlight(line, mismatches);
          return { lineIndex: line.lineIndex, flagged: highlight.lowConf || highlight.mismatch };
        }),
      );
      const target = page.lines.find((line) => line.lineIndex === index);
      if (target !== undefined) onStartEdit(target.lineIndex, target.text);
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
      tabIndex={-1}
      onKeyDown={onKeyDown}
    >
      <div className={styles.panelHeader}>
        <div className={pageStyles.titleGroup}>
          <h2 id={`${ids}-title`} ref={heading} tabIndex={-1}>
            Trang {page.pageNumber}
          </h2>
          <span className={styles.badge} data-tone={PAGE_TONE[page.status]}>
            {page.confirmed ? `${PAGE_STATUS_LABEL_VI[page.status]} · đã xác nhận` : PAGE_STATUS_LABEL_VI[page.status]}
          </span>
        </div>
        {nextTarget !== null && (
          <div className={pageStyles.nextSlot}>
            <button type="button" className={styles.secondary} onClick={onGoNext}>
              Sang trang cần xem kế tiếp
              <span className={pageStyles.nextTarget}>(trang {nextTarget})</span>
            </button>
          </div>
        )}
      </div>
      <p id={`${ids}-keys`} className={styles.hint}>
        Phím tắt trong trang này: E sửa dòng nghi ngờ đầu tiên · ← → trang cần xem lại trước, sau. Không có phím tắt xác
        nhận trang: đọc hết trang rồi bấm nút xác nhận ở cuối trang (nút chỉ nhận sau khi trang hiện được một lúc).
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
              conflictText={
                editState?.kind === "changed" && draft.editing?.lineIndex === line.lineIndex ? draft.editing.text : null
              }
              draft={editing === line.lineIndex && draft.editing !== null ? draft.editing.text : line.text}
              onDraftChange={onEditTextChange}
              onStartEdit={() => onStartEdit(line.lineIndex, line.text)}
              onCancel={() => onCloseEdit(line.lineIndex)}
              onSave={async (text) => {
                const edits = buildLineEdits(page.lines, new Map([[line.lineIndex, text]]));
                if (edits.length === 0) {
                  onCloseEdit(line.lineIndex);
                  return;
                }
                if (await onSaveLines(edits, [])) onCloseEdit(line.lineIndex);
              }}
            />
          ))}
        </ol>
      )}
      {editState?.kind === "removed" && draft.editing !== null && (
        <div className={styles.warningNote}>
          <p>
            Dòng {draft.editing.lineIndex} không còn trên trang này: trang vừa được sửa ở nơi khác. Chữ bạn đang gõ chưa
            lưu: “{typedTextLabel(draft.editing.text)}”
          </p>
          <div className={styles.actions}>
            <button
              type="button"
              className={`${styles.secondary} ${styles.small}`}
              onClick={() => {
                const lineIndex = draft.editing?.lineIndex;
                if (lineIndex !== undefined) discardAndFocusHeading(() => onCloseEdit(lineIndex));
              }}
            >
              Bỏ chữ đã gõ
            </button>
          </div>
        </div>
      )}
      {lockedNotice !== null && (
        <div className={styles.warningNote}>
          <p>Trang này không sửa được nữa nên chữ bạn đã gõ không lưu được. Chép lại nếu cần, rồi bỏ chữ đã gõ.</p>
          {lockedNotice.editing !== null && (
            <div className={styles.actions}>
              <p>
                Chữ bạn đang gõ ở dòng {lockedNotice.editing.lineIndex} chưa lưu: “
                {typedTextLabel(lockedNotice.editing.text)}”
              </p>
              <button
                type="button"
                className={`${styles.secondary} ${styles.small}`}
                aria-label={`Bỏ chữ đã gõ ở dòng ${lockedNotice.editing.lineIndex}`}
                onClick={() => {
                  const lineIndex = lockedNotice.editing?.lineIndex;
                  if (lineIndex !== undefined) discardAndFocusHeading(() => onCloseEdit(lineIndex));
                }}
              >
                Bỏ chữ đã gõ
              </button>
            </div>
          )}
          {lockedNotice.appendText !== null && (
            <div className={styles.actions}>
              <p>Chữ bạn gõ ở ô thêm dòng chưa lưu: “{lockedNotice.appendText}”</p>
              <button
                type="button"
                className={`${styles.secondary} ${styles.small}`}
                aria-label="Bỏ chữ đã gõ ở ô thêm dòng"
                onClick={() => discardAndFocusHeading(() => onAppendTextChange(""))}
              >
                Bỏ chữ đã gõ
              </button>
            </div>
          )}
        </div>
      )}
      <div className="visually-hidden" role="status">
        {announced}
      </div>
      {page.lines.length === 0 && page.status !== "failed" && page.status !== "pending" && (
        <p className={styles.muted}>Trang không có dòng chữ nào.</p>
      )}
      {editBlock === "confirmed" && <p className={styles.hint}>{EDIT_BLOCK_MESSAGE_VI.confirmed}</p>}

      {editBlock === null && (
        <AppendLine
          busy={busy}
          text={draft.appendText}
          onTextChange={onAppendTextChange}
          onAppend={(text) => onSaveLines([], [text])}
        />
      )}

      {page.status !== "failed" && page.status !== "pending" && (
        <div className={styles.actions}>
          <button
            ref={confirmButton}
            type="button"
            className={styles.primary}
            onClick={onConfirmClick}
            disabled={!confirmEnabled}
            aria-describedby={`${ids}-confirm-hint`}
          >
            {sending
              ? "Đang xác nhận…"
              : page.status === "needs_review"
                ? "Tôi đã xem lại, xác nhận trang"
                : "Xác nhận trang"}
          </button>
          <span
            id={`${ids}-confirm-hint`}
            className={buttonBlock === "unsaved_lines" ? pageStyles.unsavedNote : styles.hint}
          >
            {buttonBlock === null
              ? "Sau khi xác nhận, văn bản của trang không sửa được nữa."
              : CONFIRM_PAGE_BLOCK_MESSAGE_VI[buttonBlock]}
          </span>
        </div>
      )}
    </section>
  );
}

/** Chữ người dùng đã gõ, hiện trong câu báo xung đột; chỉ khoảng trắng thì nói rõ là để trống. */
function typedTextLabel(text: string): string {
  return text.trim() === "" ? "(để trống)" : text;
}

interface LineRowProps {
  line: LineView;
  highlight: ReturnType<typeof lineHighlight>;
  editable: boolean;
  /** Đang có thao tác ghi: Enter và nút "Lưu" không gửi thêm. */
  busy: boolean;
  editing: boolean;
  /**
   * Dòng vừa đổi ở nơi khác trong lúc ô sửa của nó đang mở (`lineEditState` là `changed`): chữ người dùng đã gõ, hiện
   * kèm nút "Sửa lại từ bản mới" và "Bỏ chữ đã gõ"; `null` khi không xung đột.
   */
  conflictText: string | null;
  /** Chữ đang gõ trong ô sửa, do `PageReview` giữ theo trang (`lib/review-drafts.ts`). */
  draft: string;
  onDraftChange: (text: string) => void;
  onStartEdit: () => void;
  onCancel: () => void;
  onSave: (text: string) => Promise<void>;
}

/** Một dòng: chữ backend trả, cờ thành nhãn tiếng Việt; sửa tại chỗ (`Enter` lưu, `Esc` huỷ). */
function LineRow({
  line,
  highlight,
  editable,
  busy,
  editing,
  conflictText,
  draft,
  onDraftChange,
  onStartEdit,
  onCancel,
  onSave,
}: LineRowProps) {
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  // Bắt đầu bằng giá trị lúc mount: dòng vẽ lại khi ô sửa vẫn mở (nháp giữ ở `PageReview`) thì không giành focus.
  const wasEditing = useRef(editing);

  // Ô sửa đóng vì xung đột (dòng vừa đổi ở nơi khác) thì không đưa focus vào nút nào, chỉ báo qua vùng thông báo của
  // trang; đóng vì Huỷ hay lưu xong thì focus về nút "Sửa" của dòng.
  const conflict = conflictText !== null;
  useEffect(() => {
    if (editing && !wasEditing.current) {
      setError(null);
      input.current?.focus();
    } else if (!editing && wasEditing.current && !conflict) {
      editButton.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing, conflict]);

  // "Bỏ chữ đã gõ" khi xung đột: nút vừa bấm biến mất, đưa focus về nút "Sửa" của dòng thay vì để rơi ra ngoài trang.
  const hadConflict = useRef(conflictText !== null);
  useEffect(() => {
    if (hadConflict.current && conflictText === null && !editing) editButton.current?.focus();
    hadConflict.current = conflictText !== null;
  }, [conflictText, editing]);

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
              onChange={(event) => onDraftChange(event.target.value)}
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
        {conflictText !== null && (
          <span className={pageStyles.unsavedNote}>
            Dòng này vừa được sửa ở nơi khác. Chữ bạn đang gõ chưa lưu: “{typedTextLabel(conflictText)}”
          </span>
        )}
      </div>
      <div className={styles.actions}>
        {conflictText !== null ? (
          <>
            {editable && (
              <button
                ref={editButton}
                type="button"
                className={`${styles.secondary} ${styles.small}`}
                onClick={onStartEdit}
                aria-label={`Sửa lại dòng ${line.lineIndex} từ bản mới`}
              >
                Sửa lại từ bản mới
              </button>
            )}
            <button
              type="button"
              className={`${styles.secondary} ${styles.small}`}
              onClick={onCancel}
              aria-label={`Bỏ chữ đã gõ ở dòng ${line.lineIndex}`}
            >
              Bỏ chữ đã gõ
            </button>
          </>
        ) : editing ? (
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
  /**
   * Chữ trong ô, do `PageReview` giữ theo trang (`lib/review-drafts.ts`): còn chữ chưa lưu thì nút xác nhận trang và nút
   * "Xác nhận các trang đọc tốt" khoá cho tới khi lưu hoặc xoá chữ.
   */
  text: string;
  onTextChange: (text: string) => void;
  onAppend: (text: string) => Promise<boolean>;
}

/** Gõ thêm một dòng vào cuối trang (`appended_lines`, §6.5), cho chữ mà bộ đọc bỏ sót. */
function AppendLine({ busy, text, onTextChange, onAppend }: AppendLineProps) {
  const ids = useId();
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (text.trim() === "") {
      setError("Nhập nội dung dòng cần thêm.");
      return;
    }
    const problem = checkLineText(text);
    setError(problem);
    if (problem !== null) return;
    if (await onAppend(text.normalize("NFC"))) onTextChange("");
  }

  return (
    <form className={styles.actions} onSubmit={(event) => void submit(event)} noValidate>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Thêm dòng vào cuối trang</span>
        <input
          className={`${styles.input} ${styles.wideInput}`}
          value={text}
          onChange={(event) => onTextChange(event.target.value)}
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
