import {
  formatNumericalAnswer,
  groupTwinsBySlot,
  optionViews,
  PROVENANCE_LABELS,
  REVIEW_STATUS_LABELS,
  shortHash,
  ticketAttemptSummary,
} from "@/lib/twin";
import type { ReviewTicket, TwinBatch, TwinQuestion } from "@/types/holocron";

import { GanttChart } from "./GanttChart";
import styles from "./TwinViewer.module.css";

interface TwinViewerProps {
  batch: TwinBatch;
}

/** Hiển thị `TwinBatch`: mọi con số (đáp án, phương án, Gantt) đều do backend trả; component không tính lại. */
export function TwinViewer({ batch }: TwinViewerProps) {
  const groups = groupTwinsBySlot(batch);
  return (
    <div className={styles.viewer}>
      <p role="status" className={styles.summary}>
        Đã sinh <strong>{batch.twins.length}</strong> Twin Question cho <strong>{groups.length}</strong> slot (seed gốc{" "}
        <code>{batch.base_seed}</code>
        {batch.review_tickets.length > 0 && <>; {batch.review_tickets.length} slot cần xem lại thủ công</>}).
      </p>
      {groups.map((group) => (
        <section key={group.slotIndex} aria-labelledby={`slot-${group.slotIndex}`} className={styles.slot}>
          <h2 id={`slot-${group.slotIndex}`}>Slot {group.slotIndex}</h2>
          {group.twins.map((twin, position) => (
            <TwinCard key={twin.twin_id} twin={twin} position={position + 1} />
          ))}
          {group.ticket !== null && <TicketNotice ticket={group.ticket} />}
        </section>
      ))}
    </div>
  );
}

function TwinCard({ twin, position }: { twin: TwinQuestion; position: number }) {
  const options = optionViews(twin);
  return (
    <article className={styles.card} aria-labelledby={`twin-${twin.twin_id}`}>
      <header className={styles.cardHeader}>
        <h3 id={`twin-${twin.twin_id}`}>Twin {position}</h3>
        <span className={twin.review_status === "AUTO_PASS" ? styles.badgeOk : styles.badgeWarn}>
          {REVIEW_STATUS_LABELS[twin.review_status]}
        </span>
      </header>

      <p className={styles.stem}>{twin.stem}</p>

      <GanttChart segments={twin.gantt} />

      {options.length > 0 && (
        <ul className={styles.options} aria-label="Phương án">
          {options.map((option) => (
            <li key={option.key} className={option.isCorrect ? styles.optionCorrect : styles.option}>
              <span className={styles.optionKey}>{option.key}.</span> {option.text}
              {option.isCorrect && <span className={styles.correctMark}> — đáp án đúng</span>}
              {option.misconception !== null && (
                <span className={styles.misconception} title={option.misconception.rationale_vi}>
                  {option.misconception.label_vi}
                  <span className={styles.misconceptionCode}> ({option.misconception.misconception_code})</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {twin.numerical_answer !== null && (
        <p>
          Đáp án số: <strong>{formatNumericalAnswer(twin.numerical_answer)}</strong> (dung sai ±
          {twin.numerical_answer.tolerance_abs})
        </p>
      )}

      <details className={styles.details}>
        <summary>Lời giải</summary>
        <p className={styles.stem}>{twin.solution_explanation_vi}</p>
        {options
          .filter((option) => option.misconception !== null)
          .map((option) => (
            <p key={option.key} className={styles.rationale}>
              <strong>Phương án {option.key}:</strong> {option.misconception?.rationale_vi}
            </p>
          ))}
      </details>

      <blockquote className={styles.citation}>
        <p>{twin.citation.verbatim_quote}</p>
        <footer>
          Trích dẫn: trang {twin.citation.page_number} — <code>{twin.citation.doc_id}</code>
        </footer>
      </blockquote>

      <p className={styles.trace}>
        Seed {twin.seed} · {PROVENANCE_LABELS[twin.trace.provenance]} (
        <code>{shortHash(twin.trace.template_sha256)}</code>) · sandbox {twin.trace.sandbox_duration_ms} ms ·{" "}
        {twin.trace.llm_attempts} lượt LLM · {twin.trace.seed_rerolls} lần đổi seed
      </p>
    </article>
  );
}

function TicketNotice({ ticket }: { ticket: ReviewTicket }) {
  const attempts = ticketAttemptSummary(ticket);
  return (
    <div role="alert" className={styles.ticket}>
      <strong>Slot {ticket.slot_index} cần xem lại thủ công.</strong> {ticket.reason}
      <span className={styles.ticketTopic}>
        {" "}
        (<code>{ticket.topic_key}</code>)
      </span>
      {attempts.length > 0 && (
        <ul className={styles.ticketAttempts}>
          {attempts.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
