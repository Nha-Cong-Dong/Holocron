"use client";

import { type RefObject, useEffect, useId, useRef, useState } from "react";

import { Accordion, Button } from "@/components/ui";
import type { ConceptView } from "@/lib/concept-board";
import type { ActionGate } from "@/lib/pairs";
import type { ConceptPair } from "@/types/holocron";

import styles from "./ConceptsTab.module.css";
import { ConceptQuote, RenameForm } from "./ProposalCard";

export interface AcceptedEntry {
  pair: ConceptPair;
  view: ConceptView;
  docTitle: string | null;
  busy: boolean;
  /** Vừa thành "đã nhận" ở lần tải này: dòng vào bằng `m-accepted-in`. */
  fresh: boolean;
  error: string | null;
}

interface AcceptedListProps {
  confirmed: readonly AcceptedEntry[];
  rejected: readonly AcceptedEntry[];
  headingRef: RefObject<HTMLHeadingElement | null>;
  onRename: (pair: ConceptPair, term: string) => void;
  onReject: (pair: ConceptPair) => void;
  onRestore: (pair: ConceptPair) => void;
}

function gateReason(gate: ActionGate): string | null {
  return gate.shown && !gate.enabled ? gate.reason : null;
}

function StarIcon() {
  return (
    <svg className={styles.rowStar} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z" fill="currentColor" />
    </svg>
  );
}

/**
 * Cột phải "Đã nhận (N)": mỗi khái niệm đã nhận là một mục thu gọn (tên + "Chương N · tr. X"); mở ra có trích dẫn,
 * "Sửa tên" và "Bỏ". Khái niệm đã bỏ nằm trong mục thu gọn "Đã bỏ (k)" kèm "Nhận lại". Cùng luật I3 với thẻ đề xuất:
 * không tra được đoạn nguồn thì không hiện số trang, không hiện như trích dẫn đã kiểm chứng, nút khoá kèm lý do. Dòng
 * vừa ghi xong mà danh sách chưa đọc lại được thì mọi nút khoá kèm lý do chờ cập nhật.
 */
export function AcceptedList({ confirmed, rejected, headingRef, onRename, onReject, onRestore }: AcceptedListProps) {
  return (
    <aside className={styles.aside} aria-labelledby="concepts-accepted-title">
      <h2 id="concepts-accepted-title" ref={headingRef} tabIndex={-1} className={styles.h2}>
        Đã nhận ({confirmed.length})
      </h2>
      {confirmed.length === 0 ? (
        <p className={styles.placeholder}>Khái niệm bạn nhận sẽ nằm ở đây, và sáng lên trên bầu trời phía trên.</p>
      ) : (
        <ul className={styles.rows}>
          {confirmed.map((entry) => (
            <AcceptedRow key={entry.pair.pair_id} entry={entry} onRename={onRename} onReject={onReject} />
          ))}
        </ul>
      )}
      {rejected.length > 0 && (
        <Accordion summary={`Đã bỏ (${rejected.length})`}>
          <ul className={styles.rows}>
            {rejected.map((entry) => (
              <RejectedRow key={entry.pair.pair_id} entry={entry} onRestore={onRestore} />
            ))}
          </ul>
        </Accordion>
      )}
    </aside>
  );
}

function AcceptedRow({
  entry,
  onRename,
  onReject,
}: {
  entry: AcceptedEntry;
  onRename: (pair: ConceptPair, term: string) => void;
  onReject: (pair: ConceptPair) => void;
}) {
  const { pair, view, busy } = entry;
  const ids = useId();
  const [editing, setEditing] = useState(false);
  const returnFocus = useRef(false);
  const editRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (editing || !returnFocus.current) return;
    returnFocus.current = false;
    editRef.current?.focus();
  }, [editing]);

  const editReason = gateReason(view.actions.edit);
  const rejectReason = gateReason(view.actions.reject);
  const reason = editReason ?? rejectReason;
  const reasonId = `${ids}-reason`;

  return (
    <li
      className={[styles.row, entry.fresh ? "m-accepted-in" : ""].join(" ")}
      data-concept-id={pair.pair_id}
      tabIndex={-1}
    >
      <Accordion
        summary={
          <span className={styles.rowSummary}>
            <StarIcon />
            <span className={styles.rowTerm}>{pair.term}</span>
            <span className={styles.rowCaption}>{view.caption}</span>
          </span>
        }
      >
        <div className={styles.rowBody}>
          <span className={styles.source}>{view.sourceLabel}</span>
          <ConceptQuote pair={pair} view={view} docTitle={entry.docTitle} />
          {editing ? (
            <RenameForm
              initial={pair.term}
              submitLabel="Lưu"
              busy={busy}
              lockedReasonId={editReason !== null ? reasonId : null}
              onSave={(term) => {
                setEditing(false);
                onRename(pair, term);
              }}
              onCancel={() => {
                returnFocus.current = true;
                setEditing(false);
              }}
            />
          ) : (
            <div className={styles.actions}>
              <button
                ref={editRef}
                type="button"
                className={styles.textButton}
                aria-disabled={busy || editReason !== null || undefined}
                aria-describedby={editReason !== null ? reasonId : undefined}
                data-concept-edit={pair.pair_id}
                onClick={() => {
                  if (!busy && editReason === null) setEditing(true);
                }}
              >
                Sửa tên
              </button>
              <Button
                variant="secondary"
                busy={busy}
                busyLabel="Đang bỏ"
                aria-disabled={rejectReason !== null || undefined}
                aria-describedby={rejectReason !== null ? reasonId : undefined}
                onClick={() => {
                  if (!busy && rejectReason === null) onReject(pair);
                }}
              >
                Bỏ
              </Button>
            </div>
          )}
          {reason !== null && (
            <p id={reasonId} className={styles.reason}>
              {reason}
            </p>
          )}
          {entry.error !== null && (
            <p className={styles.error} role="alert">
              {entry.error}
            </p>
          )}
          <p className={styles.muted}>Sửa hoặc bỏ khái niệm đã nhận sẽ đổi câu ôn tập sinh sau đó.</p>
        </div>
      </Accordion>
    </li>
  );
}

function RejectedRow({ entry, onRestore }: { entry: AcceptedEntry; onRestore: (pair: ConceptPair) => void }) {
  const { pair, view, busy } = entry;
  const ids = useId();
  const reason = gateReason(view.actions.confirm);
  const reasonId = `${ids}-reason`;
  return (
    <li className={styles.rejectedRow} data-concept-id={pair.pair_id} tabIndex={-1}>
      <span className={styles.rowSummary}>
        <span className={styles.rowTerm}>{pair.term}</span>
        <span className={styles.rowCaption}>{view.caption}</span>
      </span>
      {view.unverified !== null && <p className={styles.unverified}>{view.unverified}</p>}
      {view.actions.confirm.shown && (
        <div>
          <Button
            variant="secondary"
            busy={busy}
            busyLabel="Đang nhận lại"
            aria-disabled={reason !== null || undefined}
            aria-describedby={reason !== null ? reasonId : undefined}
            onClick={() => {
              if (!busy && reason === null) onRestore(pair);
            }}
          >
            Nhận lại
          </Button>
        </div>
      )}
      {reason !== null && (
        <p id={reasonId} className={styles.reason}>
          {reason}
        </p>
      )}
      {entry.error !== null && (
        <p className={styles.error} role="alert">
          {entry.error}
        </p>
      )}
    </li>
  );
}
