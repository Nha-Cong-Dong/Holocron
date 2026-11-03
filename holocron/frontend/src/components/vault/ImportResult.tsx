"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { ApiError, api, toUiError } from "@/lib/api";
import { createSingleFlight, reviewReasonLabel } from "@/lib/documents";
import { explainRestoreFailure } from "@/lib/dropped-pages";
import type { UiError } from "@/lib/errors";
import {
  absentJobView,
  acceptGate,
  acceptGateText,
  acceptOutcome,
  classifyConfirmConflict,
  confirmConflictError,
  droppedGroups,
  type ImportResultView,
  importSummaryLine,
  isJobActive,
  jobHeadline,
  jobLines,
  type ProposalJobView,
  pagesText,
  rejectedLines,
  shouldPollJob,
} from "@/lib/import-result";
import { createJobPoller, type JobPollEnd } from "@/lib/import-result-poll";
import { createLatestCounter } from "@/lib/latest";
import { subjectHref } from "@/lib/subject-route";
import type { VaultHrefFor } from "@/lib/vault";

import styles from "./ImportResult.module.css";
import vault from "./Vault.module.css";

type LoadState = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; result: ImportResultView };

/** Câu khi bước theo dõi job tự dừng mà job chưa xong. */
const POLL_STOPPED_VI: Record<Exclude<JobPollEnd, "finished">, string> = {
  limit: "Đã ngừng tự cập nhật tiến độ sau một thời gian dài. Bấm “Tải lại” để xem tiến độ mới nhất.",
  errors: "Đã ngừng tự cập nhật tiến độ vì không hỏi được Holocron nhiều lần liền. Bấm “Tải lại” để thử lại.",
};

interface ImportResultProps {
  documentId: string;
  subjectCode: string;
  /** Dựng địa chỉ mở một trang ở màn xem lại. */
  hrefFor: VaultHrefFor;
  /** Báo danh sách tài liệu đọc lại (sau khi chấp nhận hay khôi phục trang). */
  onChanged?: () => void;
}

/**
 * Màn kết quả nhập của một tài liệu (spec §6.5 "Màn kết quả nhập"): dòng tóm tắt từ số backend trả, lý do cần xem kèm
 * số trang, dải "Đã ẩn tự động" với nút Khôi phục, nút chính "Chấp nhận tài liệu" và tiến độ job đề xuất cặp sau khi
 * chấp nhận. Mọi số lấy nguyên từ response; component không đếm lại (I1).
 */
export function ImportResult({ documentId, subjectCode, hrefFor, onChanged }: ImportResultProps) {
  const router = useRouter();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState<UiError | null>(null);
  const [pagesLeft, setPagesLeft] = useState<{ message: string; firstPage: number | null } | null>(null);
  const [restoringPage, setRestoringPage] = useState<number | null>(null);
  const [restoreError, setRestoreError] = useState<UiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<UiError | null>(null);
  const [pollEnd, setPollEnd] = useState<Exclude<JobPollEnd, "finished"> | null>(null);
  const loads = useRef(createLatestCounter());
  // Cờ đổi ngay (không chờ render): chấp nhận, khôi phục và đề xuất lại chỉ chạy một thao tác ghi mỗi lúc.
  const writes = useRef(createSingleFlight());

  const load = useCallback(() => {
    const ticket = loads.current.begin();
    setState({ kind: "loading" });
    setPollEnd(null);
    api
      .getImportResult(documentId)
      .then((result) => {
        if (loads.current.isLatest(ticket)) setState({ kind: "ready", result });
      })
      .catch((error: unknown) => {
        if (loads.current.isLatest(ticket)) setState({ kind: "error", error: toUiError(error) });
      });
  }, [documentId]);

  /** "Tải lại": bỏ các lỗi và thông báo cũ rồi đọc lại bản mới nhất (không gửi lại thao tác ghi nào). */
  const reload = useCallback(() => {
    setAcceptError(null);
    setPagesLeft(null);
    setRestoreError(null);
    setRestartError(null);
    setNotice(null);
    load();
  }, [load]);

  /** Áp một bản mới nhất mà không quay về trạng thái "đang tải" (sau thao tác ghi hay khi theo dõi job). */
  const apply = useCallback((result: ImportResultView) => {
    loads.current.invalidate();
    setState({ kind: "ready", result });
  }, []);

  useEffect(() => {
    load();
    const counter = loads.current;
    return () => counter.invalidate();
  }, [load]);

  const result = state.kind === "ready" ? state.result : null;
  const polling = result !== null && shouldPollJob(result);

  // Theo dõi job: hỏi lại tài liệu mỗi 2 s khi job còn chạy; dừng khi job kết thúc, đủ trần lượt hoặc rời màn.
  useEffect(() => {
    if (!polling) return;
    const poller = createJobPoller({
      load: () => api.getImportResult(documentId),
      onValue: (value) => setState({ kind: "ready", result: value }),
      onEnd: (reason) => {
        if (reason !== "finished") setPollEnd(reason);
      },
    });
    poller.start();
    return () => poller.stop();
  }, [polling, documentId]);

  async function accept(current: ImportResultView): Promise<void> {
    if (!writes.current.tryEnter()) return;
    setAccepting(true);
    setAcceptError(null);
    setPagesLeft(null);
    setNotice(null);
    try {
      const after = await api.confirmImport(documentId);
      apply(after);
      onChanged?.();
      const outcome = acceptOutcome(after);
      if (outcome.kind === "pages_left") {
        setPagesLeft({ message: outcome.message, firstPage: outcome.firstPage });
        if (outcome.firstPage !== null) {
          router.push(hrefFor({ subject: subjectCode, doc: documentId, page: outcome.firstPage }));
        }
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        // Phân loại 409 bằng cách đọc lại tài liệu, không đọc chữ `detail` của backend.
        const after = await api.getImportResult(documentId).catch(() => null);
        if (after !== null) apply(after);
        setAcceptError(confirmConflictError(classifyConfirmConflict(current, after)));
      } else {
        setAcceptError(toUiError(caught));
      }
    } finally {
      writes.current.leave();
      setAccepting(false);
    }
  }

  async function restore(pageNumber: number): Promise<void> {
    if (!writes.current.tryEnter()) return;
    setRestoringPage(pageNumber);
    setRestoreError(null);
    setNotice(null);
    let expectedVersion = 0;
    try {
      const page = await api.getDocumentPage(documentId, pageNumber);
      expectedVersion = page.version;
      await api.restorePage(documentId, pageNumber, page.version);
      setNotice(`Đã khôi phục trang ${pageNumber}. Trang trở lại như mọi trang khác: xem rồi mới chấp nhận vào kho.`);
      apply(await api.getImportResult(documentId));
      onChanged?.();
    } catch (caught) {
      setRestoreError(
        expectedVersion === 0
          ? toUiError(caught)
          : await explainRestoreFailure(caught, {
              load: () => api.getDocumentPage(documentId, pageNumber),
              expectedVersion,
            }),
      );
    } finally {
      writes.current.leave();
      setRestoringPage(null);
    }
  }

  async function restartJob(): Promise<void> {
    if (!writes.current.tryEnter()) return;
    setRestarting(true);
    setRestartError(null);
    try {
      await api.startProposalJob(subjectCode, documentId);
      apply(await api.getImportResult(documentId));
      setPollEnd(null);
    } catch (caught) {
      setRestartError(toUiError(caught));
    } finally {
      writes.current.leave();
      setRestarting(false);
    }
  }

  if (state.kind === "loading") return <Loading label="Đang tải kết quả nhập…" />;
  if (state.kind === "error") return <ErrorNotice error={state.error} onRetry={reload} onReload={reload} />;

  const view = state.result;
  const gate = acceptGate(view);
  const gateText = acceptGateText(gate, view);
  const accepted = view.document.status === "confirmed";
  const summary = importSummaryLine(view);
  const groups = droppedGroups(view);

  return (
    <section className={styles.result} aria-label="Kết quả nhập tài liệu">
      {summary !== "" && <p className={styles.summary}>{summary}</p>}

      <div className={styles.block}>
        <h4 className={styles.blockTitle}>Trang cần xem</h4>
        {view.reviewReasons.length === 0 ? (
          <p className={vault.muted}>
            {view.pagesNeedsReview === 0 || accepted
              ? "Không có trang nào cần xem."
              : "Holocron chưa báo trang nào cần xem."}
          </p>
        ) : (
          <ul className={styles.reasonList}>
            {view.reviewReasons.map((group) => (
              <li key={group.reason} className={styles.reasonItem}>
                <span>
                  {reviewReasonLabel(group.reason)} ({group.count} trang)
                </span>
                {group.pages.length > 0 && (
                  <span className={styles.pageLinks}>
                    {group.pages.map((pageNumber) => (
                      <Link
                        key={pageNumber}
                        href={hrefFor({ subject: subjectCode, doc: documentId, page: pageNumber })}
                        aria-label={`Mở trang ${pageNumber} để xem lại`}
                      >
                        Trang {pageNumber}
                      </Link>
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {groups.length > 0 && (
        <div className={styles.block}>
          <h4 className={styles.blockTitle}>Đã ẩn tự động</h4>
          <p className={vault.hint}>
            Các trang này chưa vào kho tri thức và không cần xác nhận.
            {accepted ? "" : " Nếu máy ẩn nhầm, bấm “Khôi phục”."}
          </p>
          <ul className={styles.reasonList}>
            {groups.map((group) => (
              <li key={group.key} className={styles.reasonItem}>
                <span>
                  {group.label} ({pagesText(group.pages)})
                </span>
                <span className={styles.pageLinks}>
                  {group.pages.map((pageNumber) => (
                    <span key={pageNumber} className={styles.restoreItem}>
                      <Link href={hrefFor({ subject: subjectCode, doc: documentId, page: pageNumber })}>
                        Trang {pageNumber}
                      </Link>
                      {!accepted && (
                        <button
                          type="button"
                          className={`${vault.secondary} ${vault.small}`}
                          onClick={() => void restore(pageNumber)}
                          disabled={restoringPage !== null}
                        >
                          {restoringPage === pageNumber ? "Đang khôi phục…" : "Khôi phục"}
                        </button>
                      )}
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
          {restoreError !== null && <ErrorNotice error={restoreError} onReload={reload} />}
        </div>
      )}

      {notice !== null && <output className={vault.successNote}>{notice}</output>}

      <div className={styles.accept}>
        <div className={vault.actions}>
          <button
            type="button"
            className={vault.primary}
            onClick={() => void accept(view)}
            disabled={gate !== "enabled" || accepting}
          >
            {accepting ? "Đang chấp nhận…" : "Chấp nhận tài liệu"}
          </button>
        </div>
        {gateText !== null && <p className={vault.hint}>{gateText}</p>}
        {acceptError !== null && (
          <ErrorNotice error={acceptError} onRetry={() => void accept(view)} onReload={reload} />
        )}
        {pagesLeft !== null && !accepted && (
          <p className={vault.warningNote} role="status">
            {pagesLeft.message}
            {pagesLeft.firstPage !== null && (
              <>
                {" "}
                <Link href={hrefFor({ subject: subjectCode, doc: documentId, page: pagesLeft.firstPage })}>
                  Mở trang {pagesLeft.firstPage}
                </Link>
              </>
            )}
          </p>
        )}
      </div>

      {accepted && (
        <ProposalBlock
          result={view}
          subjectCode={subjectCode}
          pollEnd={pollEnd}
          restarting={restarting}
          restartError={restartError}
          onRestart={() => void restartJob()}
          onReload={reload}
        />
      )}
    </section>
  );
}

interface ProposalBlockProps {
  result: ImportResultView;
  subjectCode: string;
  pollEnd: Exclude<JobPollEnd, "finished"> | null;
  restarting: boolean;
  restartError: UiError | null;
  onRestart: () => void;
  onReload: () => void;
}

/** Khối "Đề xuất cặp khái niệm" của tài liệu đã chấp nhận: tiến độ job, hoặc lý do không có job. */
function ProposalBlock({
  result,
  subjectCode,
  pollEnd,
  restarting,
  restartError,
  onRestart,
  onReload,
}: ProposalBlockProps) {
  const conceptsHref = subjectHref(subjectCode, "concepts");
  if (result.job !== null) {
    return (
      <div className={styles.block}>
        <h4 className={styles.blockTitle}>Đề xuất cặp khái niệm</h4>
        <JobProgress job={result.job} conceptsHref={conceptsHref} />
        {pollEnd !== null && (
          <div>
            <p className={vault.warningNote}>{POLL_STOPPED_VI[pollEnd]}</p>
            <div className={vault.actions}>
              <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={onReload}>
                Tải lại
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }
  const absent = absentJobView(result.absentReason);
  if (absent.kind === "hidden") return null;
  return (
    <div className={styles.block}>
      <h4 className={styles.blockTitle}>Đề xuất cặp khái niệm</h4>
      <p className={vault.muted}>{absent.text}</p>
      {absent.kind === "configure_llm" && (
        <p>
          <Link href={conceptsHref}>Tự bôi chọn định nghĩa ở tab Khái niệm</Link>
        </p>
      )}
      {absent.kind === "restart" && (
        <>
          <div className={vault.actions}>
            <button type="button" className={vault.secondary} onClick={onRestart} disabled={restarting}>
              {restarting ? "Đang gửi…" : "Đề xuất lại"}
            </button>
          </div>
          {restartError !== null && <ErrorNotice error={restartError} onRetry={onRestart} onReload={onReload} />}
        </>
      )}
    </div>
  );
}

function JobProgress({ job, conceptsHref }: { job: ProposalJobView; conceptsHref: string }) {
  const active = isJobActive(job.status);
  const lines = jobLines(job);
  const rejected = rejectedLines(job);
  const finished = job.status === "succeeded" || job.status === "partial";
  return (
    <div>
      <p role="status" className={vault.muted}>
        {jobHeadline(job)}
      </p>
      {active && job.batchesDone !== undefined && job.batchesTotal !== undefined && (
        <progress
          className={styles.progress}
          max={job.batchesTotal}
          value={job.batchesDone}
          aria-label="Tiến độ đề xuất cặp khái niệm"
        />
      )}
      {lines.length > 0 && (
        <ul className={styles.jobLines}>
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {rejected.length > 0 && (
        <details>
          <summary>Vì sao một số đề xuất bị loại</summary>
          <ul className={styles.jobLines}>
            {rejected.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </details>
      )}
      {finished && (
        <p>
          <Link href={conceptsHref}>Xem các cặp đã đề xuất ở tab Khái niệm</Link>
        </p>
      )}
    </div>
  );
}
