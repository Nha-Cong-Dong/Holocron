"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { Icon } from "@/components/common/Icon";
import { Button, Progress } from "@/components/ui";
import { useReducedMotion } from "@/hooks/motion-hooks";
import { DOCUMENT_STATUS_LABEL_VI } from "@/lib/documents";
import { finishPlan, scheduleFinish } from "@/lib/finish-timer";
import { type ReadingRow, readingTotal } from "@/lib/import-queue";
import { NOT_SETTLED_VI, type ReviewOutcome, type ReviewProgress, remainingWorkMessages } from "@/lib/review-queue";

import styles from "./ReadingList.module.css";

interface ReadingListProps {
  rows: readonly ReadingRow[];
  /** Địa chỉ màn xem lại của một tài liệu. */
  reviewHref: (docId: string) => string;
}

/**
 * Màn 5 "Đang đọc" (design-handoff/README.md): tổng tiến độ (số tài liệu đã đọc xong) và từng tài liệu "x/y trang"
 * (số trang do backend đếm, qua `readingLines`). Thời gian còn lại chưa có dữ liệu ở backend nên chỉ có một câu báo.
 * Tài liệu đọc xong trong lúc màn mở hiện dấu tích; tài liệu không đọc được có nút "Xem lại" để xem lý do từng trang.
 * Nút "Xem lại" chỉ hiện khi tài liệu còn việc xác nhận (khác `confirmed`, §5.7: mọi trang có văn bản đã xác nhận).
 */
export function ReadingList({ rows, reviewHref }: ReadingListProps) {
  if (rows.length === 0) return null;
  const total = readingTotal(rows);
  const running = total.finished < total.total;
  return (
    <section className={styles.reading} aria-labelledby="documents-reading-title">
      <div className={styles.head}>
        <div className={styles.headTitle}>
          {running ? <HolocronMark size={28} spinning /> : <Icon name="correct" size={24} />}
          <h2 id="documents-reading-title" className={styles.title}>
            {running ? "Đang đọc tài liệu" : "Đã đọc xong"}
          </h2>
        </div>
        <output className={styles.count} aria-live="polite">
          Đã đọc xong {total.finished}/{total.total} tài liệu
        </output>
      </div>
      <Progress value={total.finished} max={total.total} unit="tài liệu" label="Tổng tiến độ đọc" active={running} />
      <p className={styles.caption}>
        Thời gian còn lại sẽ có ở bản sau. Bạn cứ rời màn này, việc đọc vẫn chạy; thẻ môn ở trang chủ có tiến độ.
      </p>
      <ul className={styles.list}>
        {rows.map((row) => (
          <ReadingItem key={row.docId} row={row} reviewHref={reviewHref} />
        ))}
      </ul>
    </section>
  );
}

function readingMeta(row: ReadingRow): string {
  if (row.kind === "done") return `Đã đọc xong · ${DOCUMENT_STATUS_LABEL_VI[row.status]}`;
  if (row.kind === "failed") return "Không đọc tiếp được";
  const progress = row.line?.progress ?? null;
  return progress === null ? "Đang đọc" : `${progress.pagesRead}/${progress.pageCount} trang`;
}

function ReadingItem({ row, reviewHref }: { row: ReadingRow; reviewHref: (docId: string) => string }) {
  const progress = row.kind === "reading" ? (row.line?.progress ?? null) : null;
  return (
    <li className={styles.item} data-kind={row.kind}>
      <span className={styles.itemIcon}>
        {row.kind === "done" ? (
          <span className={`m-done-pop ${styles.check}`}>
            <Icon name="correct" size={22} />
          </span>
        ) : (
          <Icon name={row.kind === "failed" ? "warning" : "pdf"} size={22} />
        )}
      </span>
      <div className={styles.itemBody}>
        <div className={styles.itemHead}>
          <span className={styles.itemTitle}>{row.title}</span>
          <span className={styles.itemMeta}>{readingMeta(row)}</span>
        </div>
        {progress !== null && (
          <Progress
            value={progress.pagesRead}
            max={progress.pageCount}
            unit="trang"
            label={`Tiến độ đọc ${row.title}`}
            active
          />
        )}
        {row.kind === "failed" && (
          <p className={styles.failed}>
            Holocron không đọc tiếp được tài liệu này. Bấm “Xem lại” để xem lý do của từng trang.
          </p>
        )}
      </div>
      {row.kind !== "reading" && row.status !== "confirmed" && (
        <Link className={`m-btn-secondary ${styles.link}`} href={reviewHref(row.docId)}>
          Xem lại
        </Link>
      )}
    </li>
  );
}

interface ReviewBannerProps {
  /** Số trang cần xem lại chưa xác nhận (hàng đợi theo `pages[]`). */
  queueLength: number;
  progress: ReviewProgress;
  outcome: ReviewOutcome;
  /** Mọi trang xác nhận được đã xác nhận mà tài liệu chưa chốt. */
  awaitingSettle: boolean;
}

/**
 * Banner của màn 6 "Xem lại": số trang cần xem lại, bộ đếm và thanh tiến độ (trong vùng `aria-live`), hoặc việc còn
 * lại khi không còn trang cần xem. Hàng đợi theo dòng nghi ngờ chưa có dữ liệu ở backend nên chỉ có một câu báo.
 */
export function ReviewBanner({ queueLength, progress, outcome, awaitingSettle }: ReviewBannerProps) {
  const messages = outcome.kind === "remaining" ? remainingWorkMessages(outcome.work) : [];
  return (
    <section className={styles.banner} data-tone={queueLength > 0 ? "warn" : "info"} aria-labelledby="review-banner">
      <div className={styles.bannerHead}>
        <Icon name={queueLength > 0 ? "warning" : "progress"} size={22} />
        <h3 id="review-banner" className={styles.bannerTitle}>
          {queueLength > 0
            ? `Có ${queueLength} trang cần xem lại`
            : outcome.kind === "done"
              ? "Tài liệu đã xác nhận xong"
              : "Không còn trang cần xem lại"}
        </h3>
      </div>
      <div aria-live="polite" className={styles.bannerLive}>
        {progress.total > 0 && (
          <p className={styles.counter}>
            Đã xem {progress.done}/{progress.total} trang cần xem lại
          </p>
        )}
        {queueLength === 0 && messages.length > 0 && (
          <ul className={styles.work}>
            {messages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        )}
        {queueLength === 0 && awaitingSettle && <p className={styles.counter}>{NOT_SETTLED_VI}</p>}
      </div>
      {progress.total > 0 && (
        <Progress value={progress.done} max={progress.total} unit="trang" label="Tiến độ xem lại" />
      )}
      <p className={styles.caption}>Xem lại từng dòng nghi ngờ sẽ có ở bản sau.</p>
    </section>
  );
}

interface ReviewCelebrationProps {
  title: string;
  /** Sang tab Khái niệm; không có (màn `/vault`) thì không tự chuyển và không có nút. */
  onContinue?: () => void;
}

/**
 * Xem lại xong (tài liệu `confirmed`): sao băng `m-meteor` và huy hiệu, rồi tự sang tab Khái niệm sau 1,6 s. Giảm
 * chuyển động: không tự chuyển, chỉ có nút "Sang Khái niệm" (`lib/finish-timer.ts`).
 */
export function ReviewCelebration({ title, onContinue }: ReviewCelebrationProps) {
  const reduced = useReducedMotion();
  const plan = finishPlan(reduced);
  // Hẹn giờ và nút "Sang Khái niệm" cùng dẫn tới một lần chuyển tab.
  const fired = useRef(false);
  const finish = useCallback(() => {
    if (fired.current || onContinue === undefined) return;
    fired.current = true;
    onContinue();
  }, [onContinue]);

  useEffect(() => {
    if (onContinue === undefined) return;
    return scheduleFinish({ reducedMotion: reduced, onFinish: finish });
  }, [reduced, onContinue, finish]);

  return (
    <section className={styles.celebrate} role="status" aria-labelledby="review-done-title">
      <span className={`m-meteor ${styles.meteor}`} aria-hidden="true" />
      <span className={`m-meteor ${styles.meteor} ${styles.meteor2}`} aria-hidden="true" />
      <span className={`m-celebrate-badge ${styles.badge}`} aria-hidden="true">
        <Icon name="concept" size={40} />
      </span>
      <h2 id="review-done-title" className={styles.celebrateTitle}>
        Đã xác nhận xong “{title}”
      </h2>
      <p className={styles.caption}>
        {onContinue === undefined
          ? "Văn bản của tài liệu đã được xác nhận."
          : plan.autoAdvance
            ? "Văn bản của tài liệu đã được xác nhận. Đang sang tab Khái niệm để chọn khái niệm cần ôn."
            : "Văn bản của tài liệu đã được xác nhận. Bước tiếp theo: chọn khái niệm cần ôn ở tab Khái niệm."}
      </p>
      {onContinue !== undefined && (
        <Button variant="primary" onClick={finish}>
          Sang Khái niệm
        </Button>
      )}
    </section>
  );
}
