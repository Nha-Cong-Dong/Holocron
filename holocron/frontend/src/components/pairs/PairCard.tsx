"use client";

import { useId, useState } from "react";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import {
  type ActionGate,
  type ChunkLookup,
  canSubmit,
  lookupLabel,
  PAIR_STATUS_LABEL_VI,
  type PairOperation,
  pairActions,
  pairCitation,
  termSegments,
} from "@/lib/pairs";
import { pageRangeLabel } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import { PairErrorNotice } from "./PairErrorNotice";
import styles from "./Pairs.module.css";

interface PairCardProps {
  pair: ConceptPair;
  /** Kết quả tra chunk nguồn của cặp trong map chunk của môn. */
  lookup: ChunkLookup;
  /** Đang sửa cặp này ở cột chunk: ẩn nút "Sửa". */
  isEditing: boolean;
  /** Mở chunk của cặp ở chế độ sửa. */
  onEdit: (pair: ConceptPair) => void;
  /** Xác nhận hoặc từ chối thành công: màn cha đọc lại danh sách và đưa focus về cặp này. */
  onChanged: (pairId: string) => void;
  onReloadList: () => void;
  onReloadAll: () => void;
}

const STATUS_TONE: Record<ConceptPair["status"], string> = {
  proposed: "warning",
  confirmed: "success",
  rejected: "neutral",
};

/**
 * Thuộc tính `data-pair-card` (thẻ) và `data-pair-edit` (nút "Sửa") mang `pair_id`, để màn cha tìm lại phần tử và giữ
 * focus theo `pair_id` sau khi danh sách được tải lại.
 */
export const PAIR_CARD_ATTR = "data-pair-card";
export const PAIR_EDIT_ATTR = "data-pair-edit";

/**
 * Một cặp khái niệm: trạng thái bằng chữ, thuật ngữ, đoạn định nghĩa và nút theo trạng thái (`pairActions`). Có chunk
 * nguồn trong map thì đoạn định nghĩa hiện như trích dẫn (`VerbatimQuote`, trang lấy từ chunk, tô `term` theo
 * `term_spans`); không có thì hiện chữ thường kèm nhãn chưa kiểm chứng được trang.
 */
export function PairCard({ pair, lookup, isEditing, onEdit, onChanged, onReloadList, onReloadAll }: PairCardProps) {
  const ids = useId();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<{ operation: PairOperation; error: UiError } | null>(null);
  const actions = pairActions(pair.status, lookup);
  const blocked = [actions.edit, actions.confirm].find(
    (gate): gate is Extract<ActionGate, { enabled: false }> => gate.shown && !gate.enabled,
  );

  async function run(operation: "confirm" | "reject"): Promise<void> {
    if (!canSubmit({ pending })) return;
    setPending(true);
    setFailure(null);
    try {
      if (operation === "confirm") await api.confirmConceptPair(pair.pair_id, pair.version, {});
      else await api.rejectConceptPair(pair.pair_id, pair.version);
      setPending(false);
      onChanged(pair.pair_id);
    } catch (caught) {
      setFailure({ operation, error: toUiError(caught) });
      setPending(false);
    }
  }

  const disabled = (gate: ActionGate) => !canSubmit({ pending }) || (gate.shown && !gate.enabled);

  return (
    <li
      className={styles.card}
      data-status={pair.status}
      data-pair-card={pair.pair_id}
      tabIndex={-1}
      aria-labelledby={`${ids}-term`}
    >
      <p className={styles.meta}>
        <span className={styles.badge} data-tone={STATUS_TONE[pair.status]}>
          {PAIR_STATUS_LABEL_VI[pair.status]}
        </span>
        <span>{pair.proposed_by === "llm" ? "Do LLM đề xuất" : "Do bạn tạo"}</span>
        <span>
          Chương <span className={styles.code}>{pair.chapter_code}</span>
        </span>
      </p>
      <h3 id={`${ids}-term`} className={styles.term}>
        {pair.term}
      </h3>
      {lookup.kind === "found" ? (
        <VerbatimQuote
          citation={pairCitation(pair, lookup.chunk)}
          highlights={termSegments(pair.definition_quote, pair.term_spans)}
          pageLabel={pageRangeLabel(lookup.chunk)}
        />
      ) : (
        <>
          <p className={styles.plainQuote}>{pair.definition_quote}</p>
          <p className={styles.hint}>{lookupLabel(lookup)}</p>
        </>
      )}
      {pair.status === "confirmed" && (
        <p className={styles.hint}>Sửa hoặc từ chối cặp đã xác nhận sẽ đổi câu lý thuyết sinh sau đó.</p>
      )}
      {isEditing ? (
        <p className={styles.hint}>Đang sửa ở khung văn bản của chunk.</p>
      ) : (
        <div className={styles.actions}>
          {actions.edit.shown && (
            <button
              type="button"
              className={styles.secondary}
              data-pair-edit={pair.pair_id}
              disabled={disabled(actions.edit)}
              onClick={() => onEdit(pair)}
            >
              Sửa
            </button>
          )}
          {actions.confirm.shown && (
            <button
              type="button"
              className={styles.primary}
              disabled={disabled(actions.confirm)}
              onClick={() => void run("confirm")}
            >
              {pair.status === "rejected" ? "Xác nhận lại" : "Xác nhận"}
            </button>
          )}
          {actions.reject.shown && (
            <button
              type="button"
              className={styles.secondary}
              disabled={disabled(actions.reject)}
              onClick={() => void run("reject")}
            >
              Từ chối
            </button>
          )}
          {pending && <output className={styles.hint}>Đang lưu…</output>}
        </div>
      )}
      {blocked !== undefined && !isEditing && <p className={styles.hint}>{blocked.reason}</p>}
      {failure !== null && (
        <PairErrorNotice
          error={failure.error}
          operation={failure.operation}
          onRetry={() => void run(failure.operation === "reject" ? "reject" : "confirm")}
          onReloadList={onReloadList}
          onReloadAll={onReloadAll}
        />
      )}
    </li>
  );
}
