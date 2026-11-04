"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { api, toUiError } from "@/lib/api";
import { screenErrorText } from "@/lib/concept-board";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import {
  attemptCountsLine,
  formatSubmittedAt,
  HISTORY_PAGE_SIZE,
  MODE_LABEL_VI,
  nextOffset,
} from "@/lib/practice-history";
import type { PracticeAttemptSummary } from "@/types/holocron";

import styles from "./PracticeHistory.module.css";

type Load =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; attempts: PracticeAttemptSummary[]; total: number };

interface AttemptHistoryProps {
  subjectCode: string;
  /** Về màn thiết lập đề. */
  onBack: () => void;
}

/**
 * Lịch sử làm bài của một môn (§6.12): các lượt mới nhất trước, kèm thời gian, hình thức và kết quả. Thời gian, số đếm
 * và tổng số lượt do backend trả; màn này chỉ hiển thị và tải thêm theo `offset`.
 */
export function AttemptHistory({ subjectCode, onBack }: AttemptHistoryProps) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [moreError, setMoreError] = useState<UiError | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const calls = useRef(createLatestCounter());

  const reload = useCallback(() => {
    const ticket = calls.current.begin();
    setLoad({ kind: "loading" });
    setMoreError(null);
    api.listPracticeAttempts(subjectCode, 0, HISTORY_PAGE_SIZE).then(
      (page) => {
        if (calls.current.isLatest(ticket)) setLoad({ kind: "ready", attempts: page.attempts, total: page.total });
      },
      (error: unknown) => {
        if (calls.current.isLatest(ticket)) setLoad({ kind: "error", error: toUiError(error) });
      },
    );
  }, [subjectCode]);

  useEffect(() => {
    reload();
    const counter = calls.current;
    return () => counter.invalidate();
  }, [reload]);

  const loadMore = useCallback(
    (offset: number) => {
      const ticket = calls.current.begin();
      setLoadingMore(true);
      setMoreError(null);
      api.listPracticeAttempts(subjectCode, offset, HISTORY_PAGE_SIZE).then(
        (page) => {
          if (!calls.current.isLatest(ticket)) return;
          setLoadingMore(false);
          setLoad((current) =>
            current.kind === "ready"
              ? { kind: "ready", attempts: [...current.attempts, ...page.attempts], total: page.total }
              : current,
          );
        },
        (error: unknown) => {
          if (!calls.current.isLatest(ticket)) return;
          setLoadingMore(false);
          setMoreError(toUiError(error));
        },
      );
    },
    [subjectCode],
  );

  return (
    <section className={styles.root} aria-labelledby="attempt-history-title">
      <div className={styles.head}>
        <h2 id="attempt-history-title" className={styles.title}>
          Lịch sử làm bài
        </h2>
        <Button variant="secondary" onClick={onBack}>
          Quay lại
        </Button>
      </div>

      {load.kind === "loading" && (
        <div aria-busy="true">
          <Skeleton height={56} />
          <Skeleton height={56} />
          <p className={styles.meta}>Đang tải lịch sử…</p>
        </div>
      )}
      {load.kind === "error" && (
        <ErrorState
          title="Không tải được lịch sử làm bài"
          fix={screenErrorText(load.error)}
          onRetry={reload}
          technical={load.error.diagnostic}
        />
      )}
      {load.kind === "ready" && load.attempts.length === 0 && (
        <EmptyState
          title="Chưa có lượt làm bài nào"
          description="Làm một đề ôn tập xong thì kết quả sẽ hiện ở đây."
          action={{ label: "Về thiết lập đề", onClick: onBack }}
        />
      )}
      {load.kind === "ready" && load.attempts.length > 0 && (
        <>
          <ul className={styles.list}>
            {load.attempts.map((attempt) => (
              <li key={attempt.attempt_id} className={styles.row}>
                <div className={styles.rowMain}>
                  <span className={styles.when}>{formatSubmittedAt(attempt.submitted_at)}</span>
                  <span className={styles.meta}>{attemptCountsLine(attempt)}</span>
                </div>
                <span className={styles.badge}>{MODE_LABEL_VI[attempt.mode]}</span>
              </li>
            ))}
          </ul>
          <div className={styles.pager}>
            <span aria-live="polite">
              Hiện {load.attempts.length}/{load.total} lượt
            </span>
            {nextOffset(0, load.attempts.length, load.total) !== null && (
              <Button variant="secondary" busy={loadingMore} onClick={() => loadMore(load.attempts.length)}>
                {loadingMore ? "Đang tải…" : "Tải thêm"}
              </Button>
            )}
          </div>
          {moreError !== null && (
            <p className={styles.notice} role="alert">
              {screenErrorText(moreError)}
            </p>
          )}
        </>
      )}
    </section>
  );
}
