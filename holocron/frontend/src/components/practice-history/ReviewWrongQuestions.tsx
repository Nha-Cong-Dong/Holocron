"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { McqQuestionView } from "@/components/practice/McqQuestionView";
import { Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { api, toUiError } from "@/lib/api";
import { screenErrorText } from "@/lib/concept-board";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import type { McqQuestion } from "@/lib/practice-data";
import { formatSubmittedAt, REVIEW_PAGE_SIZE, type ReviewItem, type ReviewPageView } from "@/lib/practice-history";
import { sourceCaption } from "@/lib/quiz";
import type { OptionKey } from "@/types/holocron";

import styles from "./PracticeHistory.module.css";

type Load = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; page: ReviewPageView };

type Save = "saving" | "saved" | "failed";

interface ReviewWrongQuestionsProps {
  subjectCode: string;
  /** Về màn thiết lập đề. */
  onBack: () => void;
}

/**
 * Ôn câu sai của một môn (§6.12). Danh sách và `total` lấy từ backend theo luật "câu sai còn mở"; làm lại một câu bằng
 * thành phần câu hỏi của màn luyện tập, hiển thị đáp án và trích dẫn nguyên văn backend trả (I3). Chọn xong thì ghi một
 * lượt `review`; câu làm đúng chỉ rời danh sách sau khi tải lại từ backend (không tự lọc cục bộ).
 */
export function ReviewWrongQuestions({ subjectCode, onBack }: ReviewWrongQuestionsProps) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [open, setOpen] = useState<ReviewItem | null>(null);
  const calls = useRef(createLatestCounter());

  const reload = useCallback(() => {
    const ticket = calls.current.begin();
    setLoad({ kind: "loading" });
    api.listReviewQuestions(subjectCode, 0, REVIEW_PAGE_SIZE).then(
      (page) => {
        if (calls.current.isLatest(ticket)) setLoad({ kind: "ready", page });
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

  const backToList = useCallback(() => {
    setOpen(null);
    reload();
  }, [reload]);

  if (open !== null && open.question.kind === "mcq") {
    return (
      <ReviewOne key={open.question.twinId} subjectCode={subjectCode} question={open.question} onDone={backToList} />
    );
  }

  return (
    <section className={styles.root} aria-labelledby="review-wrong-title">
      <div className={styles.head}>
        <h2 id="review-wrong-title" className={styles.title}>
          Ôn câu sai
        </h2>
        <Button variant="secondary" onClick={onBack}>
          Quay lại
        </Button>
      </div>

      {load.kind === "loading" && (
        <div aria-busy="true">
          <Skeleton height={56} />
          <Skeleton height={56} />
          <p className={styles.meta}>Đang tải câu cần ôn…</p>
        </div>
      )}
      {load.kind === "error" && (
        <ErrorState
          title="Không tải được danh sách câu sai"
          fix={screenErrorText(load.error)}
          onRetry={reload}
          technical={load.error.diagnostic}
        />
      )}
      {load.kind === "ready" && load.page.items.length === 0 && (
        <EmptyState
          title="Không có câu sai nào cần ôn"
          description="Câu sai ở các lượt làm bài sẽ hiện ở đây cho đến khi bạn làm đúng lại."
          action={{ label: "Về thiết lập đề", onClick: onBack }}
        />
      )}
      {load.kind === "ready" && load.page.items.length > 0 && (
        <>
          <p className={styles.meta} aria-live="polite">
            {load.page.total} câu cần ôn
          </p>
          <ul className={styles.list}>
            {load.page.items.map((item) => (
              <li key={item.question.twinId} className={styles.row}>
                <div className={styles.rowMain}>
                  <span className={styles.stemPreview}>{item.question.stem}</span>
                  <span className={styles.meta}>Sai lần gần nhất: {formatSubmittedAt(item.lastAnsweredAt)}</span>
                </div>
                <Button variant="secondary" onClick={() => setOpen(item)}>
                  Ôn câu này
                </Button>
              </li>
            ))}
          </ul>
          {load.page.total > load.page.items.length && (
            <p className={styles.meta}>Đang hiện {load.page.items.length} câu đầu; ôn xong sẽ có câu tiếp theo.</p>
          )}
          {load.page.undisplayable > 0 && (
            <p className={styles.notice}>
              Có {load.page.undisplayable} câu Holocron trả về mà giao diện này chưa hiển thị được.
            </p>
          )}
        </>
      )}
    </section>
  );
}

interface ReviewOneProps {
  subjectCode: string;
  question: McqQuestion;
  /** Xong câu này: tải lại danh sách từ backend. */
  onDone: () => void;
}

/** Làm lại một câu: chọn phương án, ghi lượt `review`, hiện đáp án và trích dẫn nguyên văn của câu đã lưu. */
function ReviewOne({ subjectCode, question, onDone }: ReviewOneProps) {
  const [selected, setSelected] = useState<OptionKey | null>(null);
  const [save, setSave] = useState<Save | null>(null);
  const [titles, setTitles] = useState<ReadonlyMap<string, string>>(new Map());
  const pending = useRef<OptionKey | null>(null);

  const submit = useCallback(
    (key: OptionKey) => {
      setSave("saving");
      api
        .recordPracticeAttempt({
          mode: "review",
          batch_id: null,
          answers: [{ twin_id: question.twinId, chosen_key: key }],
        })
        .then(
          () => setSave("saved"),
          () => setSave("failed"),
        );
    },
    [question.twinId],
  );

  const pick = useCallback(
    (key: OptionKey) => {
      if (pending.current !== null) return;
      pending.current = key;
      setSelected(key);
      submit(key);
    },
    [submit],
  );

  // Tên tài liệu cho chú thích trích dẫn: không tải được thì chỉ ghi số trang.
  useEffect(() => {
    let alive = true;
    api.listDocuments(subjectCode).then(
      (documents) => {
        if (alive) setTitles(new Map(documents.map((document) => [document.docId, document.title])));
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [subjectCode]);

  const answered = selected !== null;
  const caption = sourceCaption(question.citation, titles);
  return (
    <section className={styles.root} aria-label="Ôn một câu sai">
      <article className={styles.card}>
        <h2 className={styles.stem}>{question.stem}</h2>
        <McqQuestionView
          question={question}
          entry={{ kind: "mcq", selected, submitted: answered }}
          titles={titles}
          onPick={pick}
        />
        {answered && (
          <div className={styles.quote}>
            <p className={styles.quoteLabel}>Nguồn</p>
            <VerbatimQuote citation={question.citation} pageLabel={caption.pageLabel} sourceTitle={caption.title} />
          </div>
        )}
        {save === "failed" && (
          <p className={styles.notice} role="status">
            Chưa lưu được kết quả ôn lần này.{" "}
            <Button variant="secondary" onClick={() => selected !== null && submit(selected)}>
              Thử lại
            </Button>
          </p>
        )}
        {answered && (
          <div className={styles.head}>
            <span className={styles.meta}>
              {save === "saved" ? "Đã lưu kết quả ôn." : save === "saving" ? "Đang lưu kết quả…" : ""}
            </span>
            <Button variant="primary" onClick={onDone} busy={save === "saving"}>
              Về danh sách
            </Button>
          </div>
        )}
      </article>
    </section>
  );
}
