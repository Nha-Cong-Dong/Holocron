"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { TesseractHelp } from "@/components/common/TesseractHelp";
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
import type { DocumentView, LineView, PageStatusView, PageView } from "@/lib/documents-data";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import type { Poller } from "@/lib/poll";
import { vaultHref } from "@/lib/vault";

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
  pageNumber: number;
}

/**
 * Xem lại một trang (`/vault?doc=…&page=…`; §5.7, §6.5): danh sách trang bên trái, văn bản theo dòng bên phải. Dòng
 * có cờ conf thấp hoặc token lệch được tô theo dữ liệu backend; sửa dòng gửi `expected_version`, 409 lệch phiên bản
 * thì có nút tải lại bản mới nhất; xác nhận từng trang (`ok`, `needs_review`) và xác nhận cả tài liệu (chỉ trang `ok`).
 * Chưa có ảnh trang vì chưa có cách lấy file gốc (kế hoạch UI M12).
 */
export function PageReview({ documentId, pageNumber }: PageReviewProps) {
  const router = useRouter();
  const [document, setDocument] = useState<Loadable<DocumentView>>({ kind: "loading" });
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
        if (docCalls.current.isLatest(ticket)) setDocument({ kind: "ready", value });
      })
      .catch((error: unknown) => {
        if (docCalls.current.isLatest(ticket)) setDocument({ kind: "error", error: toUiError(error) });
      });
  }, [documentId]);

  const loadPage = useCallback(() => {
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
    if (!pendingPage) return;
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
      setNotice("Đã xác nhận các trang đọc tốt. Trang cần xem kỹ vẫn chờ bạn xác nhận từng trang.");
      loadPage();
    } catch (caught) {
      setActionError(toUiError(caught));
    } finally {
      writes.current.leave();
      setBusy(false);
    }
  }, [documentId, loadPage]);

  if (document.kind === "loading") return <Loading label="Đang tải tài liệu…" />;
  if (document.kind === "error") {
    return (
      <div className={styles.page}>
        <ErrorNotice error={document.error} onRetry={loadDocument} onReload={loadDocument} />
        <p>
          <Link href="/vault">Về danh sách môn</Link>
        </p>
      </div>
    );
  }
  const doc = document.value;
  const docBlock = confirmDocumentBlock(doc);
  const subjectHref = vaultHref({ subject: doc.subjectCode });

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
      </section>

      <div className={styles.review}>
        <nav aria-label="Các trang của tài liệu">
          <ol className={styles.pageNav}>
            {doc.pages.map((item) => (
              <li key={item.pageNumber}>
                <Link
                  className={styles.pageLink}
                  data-status={item.status}
                  href={vaultHref({ subject: doc.subjectCode, doc: doc.docId, page: item.pageNumber })}
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
          {page.kind === "loading" && <Loading label={`Đang tải trang ${pageNumber}…`} />}
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
              onDelete={() => setDeleting(true)}
              onReload={reloadAll}
              onSaveLines={(edits, appended) =>
                runWrite(
                  () => api.patchPageLines(documentId, pageNumber, page.value.version, edits, appended),
                  "Đã lưu dòng vừa sửa.",
                )
              }
              onConfirm={() =>
                runWrite(() => api.confirmPage(documentId, pageNumber, page.value.version), "Đã xác nhận trang.")
              }
            />
          )}
          <PageSteps doc={doc} pageNumber={pageNumber} />
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

function PageSteps({ doc, pageNumber }: { doc: DocumentView; pageNumber: number }) {
  const index = doc.pages.findIndex((item) => item.pageNumber === pageNumber);
  const previous = index > 0 ? doc.pages[index - 1] : undefined;
  const next = index >= 0 ? doc.pages[index + 1] : undefined;
  return (
    <nav className={styles.pager} aria-label="Chuyển trang">
      {previous !== undefined && (
        <Link href={vaultHref({ subject: doc.subjectCode, doc: doc.docId, page: previous.pageNumber })}>
          ← Trang {previous.pageNumber}
        </Link>
      )}
      {next !== undefined && (
        <Link href={vaultHref({ subject: doc.subjectCode, doc: doc.docId, page: next.pageNumber })}>
          Trang {next.pageNumber} →
        </Link>
      )}
    </nav>
  );
}

interface PagePanelProps {
  page: PageView;
  busy: boolean;
  onSaveLines: (edits: ReturnType<typeof buildLineEdits>, appended: string[]) => Promise<boolean>;
  onConfirm: () => Promise<boolean>;
  onDelete: () => void;
  onReload: () => void;
}

function PagePanel({ page, busy, onSaveLines, onConfirm, onDelete, onReload }: PagePanelProps) {
  const ids = useId();
  const editBlock = lineEditBlock(page);
  const confirmBlock = confirmPageBlock(page);
  const mismatches = mismatchesByLine(page.crossCheck.mismatches);
  const warnings = pageWarnings({ ...page, failureReason: page.failureReason });
  const [editing, setEditing] = useState<number | null>(null);

  return (
    <section className={styles.panel} aria-labelledby={`${ids}-title`}>
      <div className={styles.panelHeader}>
        <h2 id={`${ids}-title`}>Trang {page.pageNumber}</h2>
        <span className={styles.badge} data-tone={PAGE_TONE[page.status]}>
          {page.confirmed ? `${PAGE_STATUS_LABEL_VI[page.status]} · đã xác nhận` : PAGE_STATUS_LABEL_VI[page.status]}
        </span>
      </div>

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

      {editBlock === null && <AppendLine busy={busy} onAppend={(text) => onSaveLines([], [text])} />}

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

/** Gõ thêm một dòng vào cuối trang (`appended_lines`, §6.5), cho chữ mà bộ đọc bỏ sót. */
function AppendLine({ busy, onAppend }: { busy: boolean; onAppend: (text: string) => Promise<boolean> }) {
  const ids = useId();
  const [text, setText] = useState("");
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
