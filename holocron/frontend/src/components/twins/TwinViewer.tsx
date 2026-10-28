import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { ticketLine } from "@/lib/practice";
import {
  formatNumericalAnswer,
  groupTwinsBySlot,
  isTheoryTwin,
  type OptionView,
  optionViews,
  PROVENANCE_LABELS,
  REVIEW_STATUS_LABELS,
  shortHash,
  THEORY_FORM_LABELS,
  ticketAttemptSummary,
} from "@/lib/twin";
import type { ReviewTicket, TheoryTwin, TwinBatch, TwinQuestion } from "@/types/holocron";

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
        Đã sinh <strong>{batch.twins.length}</strong> câu song sinh cho <strong>{groups.length}</strong> câu gốc (seed
        gốc <code>{batch.base_seed}</code>
        {batch.review_tickets.length > 0 && <>; {batch.review_tickets.length} câu gốc cần xem lại thủ công</>}).
      </p>
      {groups.map((group) => (
        <section key={group.slotIndex} aria-labelledby={`source-${group.slotIndex}`} className={styles.slot}>
          <h2 id={`source-${group.slotIndex}`}>Câu gốc {group.slotIndex}</h2>
          {group.twins.map((twin, position) =>
            isTheoryTwin(twin) ? (
              <TheoryTwinCard key={twin.twin_id} twin={twin} position={position + 1} />
            ) : (
              <TwinCard key={twin.twin_id} twin={twin} position={position + 1} />
            ),
          )}
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
        <h3 id={`twin-${twin.twin_id}`}>Câu song sinh {position}</h3>
        <span className={twin.review_status === "AUTO_PASS" ? styles.badgeOk : styles.badgeWarn}>
          {REVIEW_STATUS_LABELS[twin.review_status]}
        </span>
      </header>

      <p className={styles.stem}>{twin.stem}</p>

      <GanttChart segments={twin.gantt} />

      <OptionList options={options} />
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

      <VerbatimQuote citation={twin.citation} />

      <p className={styles.trace}>
        Seed {twin.seed} · {PROVENANCE_LABELS[twin.trace.provenance]} (
        <code>{shortHash(twin.trace.template_sha256)}</code>) · tính trong {twin.trace.sandbox_duration_ms} ms ·{" "}
        {twin.trace.llm_attempts} lượt LLM · {twin.trace.seed_rerolls} lần đổi seed
      </p>
    </article>
  );
}

function OptionList({ options }: { options: OptionView[] }) {
  if (options.length === 0) return null;
  return (
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
  );
}

/**
 * Câu lý thuyết (`TheoryTwin`, quyết định 14(b)): phương án, đáp án, misconception và trích dẫn đều do backend trả;
 * mỗi phương án nhiễu kèm trích dẫn nguyên văn của khái niệm nó lấy từ (`distractor_citations`, I3).
 */
function TheoryTwinCard({ twin, position }: { twin: TheoryTwin; position: number }) {
  const question = twin.question;
  const options = optionViews(question);
  return (
    <article className={styles.card} aria-labelledby={`twin-${twin.twin_id}`}>
      <header className={styles.cardHeader}>
        <h3 id={`twin-${twin.twin_id}`}>Câu song sinh {position}</h3>
        <span className={twin.review_status === "AUTO_PASS" ? styles.badgeOk : styles.badgeWarn}>
          {REVIEW_STATUS_LABELS[twin.review_status]}
        </span>
      </header>

      <p className={styles.stem}>{question.stem}</p>

      <OptionList options={options} />

      <details className={styles.details}>
        <summary>Lời giải</summary>
        <p className={styles.stem}>{question.solution_explanation_vi}</p>
        {options.map((option) => {
          const citation = question.distractor_citations[option.key];
          if (option.misconception === null || citation === undefined) return null;
          return (
            <div key={option.key} className={styles.rationale}>
              <strong>Phương án {option.key}:</strong> {option.misconception.rationale_vi}
              <VerbatimQuote citation={citation} />
            </div>
          );
        })}
      </details>

      <VerbatimQuote citation={question.citation} />

      <p className={styles.trace}>
        Câu lý thuyết · {THEORY_FORM_LABELS[question.form]} · chương {question.chapter_code} · seed {question.seed} · bộ
        sinh {question.generator_version} (<code>{shortHash(question.pool_sha256)}</code>)
      </p>
    </article>
  );
}

function TicketNotice({ ticket }: { ticket: ReviewTicket }) {
  const attempts = ticketAttemptSummary(ticket);
  return (
    <div role="alert" className={styles.ticket}>
      <strong>Câu gốc {ticket.slot_index} cần xem lại thủ công.</strong>{" "}
      {ticketLine({ reason: ticket.reason, theory: ticket.topic_key === null })}
      {ticket.topic_key !== null && (
        <span className={styles.ticketTopic}>
          {" "}
          (<code>{ticket.topic_key}</code>)
        </span>
      )}
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
