"use client";

import type { RefObject } from "react";

import { Button } from "@/components/ui";
import type { ConceptView } from "@/lib/concept-board";
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
}

interface ProposalListProps {
  entries: readonly ProposalEntry[];
  headingRef: RefObject<HTMLHeadingElement | null>;
  /** "Nhận tất cả" đang gửi: nút bận, bấm lại không có tác dụng. */
  bulkRunning: boolean;
  onAcceptAll: () => void;
  onAccept: (pair: ConceptPair, term: string | null) => void;
  onReject: (pair: ConceptPair) => void;
  onRetry: (pair: ConceptPair) => void;
  onLeft: (pair: ConceptPair) => void;
}

/** Khối "Đề xuất (N)": tiêu đề, nút chính duy nhất "Nhận tất cả" và các thẻ đề xuất theo thứ tự backend trả. */
export function ProposalList({
  entries,
  headingRef,
  bulkRunning,
  onAcceptAll,
  onAccept,
  onReject,
  onRetry,
  onLeft,
}: ProposalListProps) {
  return (
    <section className={styles.main} aria-labelledby="concepts-proposals-title">
      <div className={styles.blockHead}>
        <div>
          <h2 id="concepts-proposals-title" ref={headingRef} tabIndex={-1} className={styles.h2}>
            Đề xuất ({entries.length})
          </h2>
          <p className={styles.muted}>Mỗi khái niệm kèm đoạn trích nguyên văn. Nhận cái nào đúng ý bạn.</p>
        </div>
        <Button variant="primary" busy={bulkRunning} busyLabel="Đang nhận" onClick={onAcceptAll}>
          Nhận tất cả
        </Button>
      </div>
      <ul className={styles.cards}>
        {entries.map((entry) => (
          <ProposalCard
            key={entry.pair.pair_id}
            pair={entry.pair}
            view={entry.view}
            docTitle={entry.docTitle}
            busy={entry.busy}
            leaving={entry.leaving}
            error={entry.error}
            onAccept={(term) => onAccept(entry.pair, term)}
            onReject={() => onReject(entry.pair)}
            onRetry={() => onRetry(entry.pair)}
            onLeft={() => onLeft(entry.pair)}
          />
        ))}
      </ul>
    </section>
  );
}
