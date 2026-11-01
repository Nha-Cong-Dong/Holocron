"use client";

import { type RefObject, useEffect, useId, useRef } from "react";

import { Button } from "@/components/ui";
import type { ConceptView } from "@/lib/concept-board";
import type { BatchButtons, SelectAllState } from "@/lib/concept-proposals";
import type { ConceptPair } from "@/types/holocron";

import styles from "./ConceptsTab.module.css";
import { type CardLeaving, ProposalCard } from "./ProposalCard";

export interface ProposalEntry {
  pair: ConceptPair;
  view: ConceptView;
  docTitle: string | null;
  busy: boolean;
  leaving: CardLeaving;
  error: string | null;
  selected: boolean;
  selectable: boolean;
  termDraft: string | null;
  /** Lý do thuộc nhóm "Cần kiểm tra" (theo `failed_gates` backend trả); `null` ⇒ không thuộc nhóm. */
  checkReason: string | null;
}

interface ProposalListProps {
  entries: readonly ProposalEntry[];
  headingRef: RefObject<HTMLHeadingElement | null>;
  selectAll: SelectAllState;
  /** Số cặp đang được chọn (trong số chọn được). */
  chosenCount: number;
  buttons: BatchButtons;
  /** Đang gửi hàng đợi: dòng tiến độ ("Đang chấp nhận 3/12…"), đọc qua `aria-live`. */
  progress: string | null;
  /** Câu tóm tắt lượt gửi vừa xong; `null` khi chưa có. */
  result: string | null;
  acceptRunning: boolean;
  rejectRunning: boolean;
  onToggleAll: () => void;
  onToggle: (pair: ConceptPair) => void;
  onSaveTerm: (pair: ConceptPair, term: string) => void;
  onShowSource: (pair: ConceptPair) => void;
  onAcceptSelected: () => void;
  onRejectSelected: () => void;
  onAccept: (pair: ConceptPair, term: string | null) => void;
  onReject: (pair: ConceptPair) => void;
  onRetry: (pair: ConceptPair) => void;
  onLeft: (pair: ConceptPair) => void;
}

/**
 * Khối "Đề xuất (N)": hộp "Chọn tất cả" ba trạng thái, "Chấp nhận & ôn tập" và "Từ chối đã chọn" (gửi tuần tự từng
 * cặp), rồi các thẻ theo thứ tự backend trả; cặp có `failed_gates` nằm riêng ở nhóm "Cần kiểm tra". Tiến độ và kết quả
 * của lượt gửi nằm trong vùng `aria-live`.
 */
export function ProposalList({
  entries,
  headingRef,
  selectAll,
  chosenCount,
  buttons,
  progress,
  result,
  acceptRunning,
  rejectRunning,
  onToggleAll,
  onToggle,
  onSaveTerm,
  onShowSource,
  onAcceptSelected,
  onRejectSelected,
  onAccept,
  onReject,
  onRetry,
  onLeft,
}: ProposalListProps) {
  const reasonId = useId();
  const allRef = useRef<HTMLInputElement>(null);
  const anySelectable = entries.some((entry) => entry.selectable);
  const ready = entries.filter((entry) => entry.checkReason === null);
  const checking = entries.filter((entry) => entry.checkReason !== null);

  useEffect(() => {
    if (allRef.current !== null) allRef.current.indeterminate = selectAll === "some";
  }, [selectAll]);

  const renderCard = (entry: ProposalEntry) => (
    <ProposalCard
      key={entry.pair.pair_id}
      pair={entry.pair}
      view={entry.view}
      docTitle={entry.docTitle}
      busy={entry.busy}
      leaving={entry.leaving}
      error={entry.error}
      selected={entry.selected}
      selectable={entry.selectable}
      termDraft={entry.termDraft}
      checkReason={entry.checkReason}
      onToggle={() => onToggle(entry.pair)}
      onSaveTerm={(term) => onSaveTerm(entry.pair, term)}
      onShowSource={() => onShowSource(entry.pair)}
      onAccept={(term) => onAccept(entry.pair, term)}
      onReject={() => onReject(entry.pair)}
      onRetry={() => onRetry(entry.pair)}
      onLeft={() => onLeft(entry.pair)}
    />
  );

  return (
    <section className={styles.main} aria-labelledby="concepts-proposals-title">
      <div>
        <h2 id="concepts-proposals-title" ref={headingRef} tabIndex={-1} className={styles.h2}>
          Đề xuất ({entries.length})
        </h2>
        <p className={styles.muted}>
          Mỗi khái niệm kèm đoạn trích nguyên văn. Tích những cái đúng ý bạn rồi bấm Chấp nhận. Ô đã tích sẵn chỉ là gợi
          ý của máy, chưa phải đã chấp nhận.
        </p>
      </div>
      <div className={styles.toolbar}>
        <div className={styles.toolbarRow}>
          <label className={styles.checkLabel}>
            <input
              ref={allRef}
              type="checkbox"
              className={styles.check}
              checked={selectAll === "all"}
              disabled={!anySelectable || acceptRunning || rejectRunning}
              onChange={onToggleAll}
            />
            Chọn tất cả
          </label>
          <span className={styles.muted}>{chosenCount > 0 ? `Đã chọn ${chosenCount}` : "Chưa chọn khái niệm nào"}</span>
          <div className={styles.actions}>
            <Button
              variant="secondary"
              busy={rejectRunning}
              busyLabel="Đang từ chối"
              aria-disabled={!buttons.rejectEnabled || undefined}
              aria-describedby={buttons.reason !== null ? reasonId : undefined}
              onClick={() => {
                if (buttons.rejectEnabled) onRejectSelected();
              }}
            >
              Từ chối đã chọn
            </Button>
            <Button
              variant="primary"
              busy={acceptRunning}
              busyLabel="Đang chấp nhận"
              aria-disabled={!buttons.acceptEnabled || undefined}
              aria-describedby={buttons.reason !== null ? reasonId : undefined}
              onClick={() => {
                if (buttons.acceptEnabled) onAcceptSelected();
              }}
            >
              Chấp nhận &amp; ôn tập
            </Button>
          </div>
        </div>
        {buttons.reason !== null && (
          <p id={reasonId} className={styles.reason}>
            {buttons.reason}
          </p>
        )}
        <div aria-live="polite" aria-atomic="true">
          {progress !== null && <p className={styles.progress}>{progress}</p>}
          {progress === null && result !== null && <p className={styles.batchResult}>{result}</p>}
        </div>
      </div>
      {ready.length > 0 && <ul className={styles.cards}>{ready.map(renderCard)}</ul>}
      {checking.length > 0 && (
        <>
          <h3 className={styles.groupTitle}>Cần kiểm tra ({checking.length})</h3>
          <p className={styles.muted}>
            Máy thấy các khái niệm này có điểm cần xem lại. Chúng không tự được tích sẵn; đọc kỹ đoạn trích trước khi
            chấp nhận.
          </p>
          <ul className={styles.cards}>{checking.map(renderCard)}</ul>
        </>
      )}
    </section>
  );
}
