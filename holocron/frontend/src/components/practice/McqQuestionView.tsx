"use client";

import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { Accordion } from "@/components/ui";
import { withContentKeys } from "@/lib/keys";
import type { AnswerEntry } from "@/lib/practice";
import type { McqQuestion } from "@/lib/practice-data";
import { answerAnnouncement, type DistractorNote, distractorNotes, optionStates, sourceCaption } from "@/lib/quiz";
import type { OptionKey } from "@/types/holocron";

import { ArtifactView } from "./ArtifactView";
import { OptionList } from "./OptionList";
import styles from "./Quiz.module.css";

type McqEntry = Extract<AnswerEntry, { kind: "mcq" }>;

interface McqQuestionViewProps {
  question: McqQuestion;
  entry: McqEntry;
  /** Tên tài liệu theo mã tài liệu, để ghi dưới trích dẫn (không có thì chỉ ghi trang). */
  titles: ReadonlyMap<string, string>;
  onPick: (key: OptionKey) => void;
}

/**
 * Một câu trắc nghiệm (màn 9): bấm phương án hoặc phím 1–4/A–D là chọn và khoá ngay. Sau khi chọn: kết quả trên từng
 * phương án, giải thích phương án sai đã chọn ngay dưới danh sách, các phương án sai khác trong mục thu gọn, và chỉ câu
 * tính toán mới có "Giải thích" cùng bảng/biểu đồ lời giải. Đúng/sai là phép so khoá với `correct_key` backend trả.
 */
export function McqQuestionView({ question, entry, titles, onPick }: McqQuestionViewProps) {
  const notes = distractorNotes(question, entry);
  const answered = entry.submitted && entry.selected !== null;

  return (
    <>
      <OptionList
        options={optionStates(question, entry)}
        onPick={onPick}
        announcement={answerAnnouncement(question, entry)}
      />

      {notes.chosen !== null && (
        <section className={styles.note} aria-label="Vì sao phương án đã chọn chưa đúng">
          <NoteBody note={notes.chosen} titles={titles} />
        </section>
      )}

      {notes.others.length > 0 && (
        <Accordion summary="Vì sao các phương án sai khác không đúng">
          <div className={styles.notes}>
            {notes.others.map((note) => (
              <div key={note.key} className={styles.note}>
                <NoteBody note={note} titles={titles} />
              </div>
            ))}
          </div>
        </Accordion>
      )}

      {answered && question.questionKind === "computational" && (
        <section className={styles.note} aria-labelledby={`solution-${question.twinId}`}>
          <h3 id={`solution-${question.twinId}`} className={styles.sectionTitle}>
            Giải thích
          </h3>
          <p className={styles.solution}>{question.solution}</p>
          {withContentKeys(question.artifacts, (artifact) => JSON.stringify(artifact)).map(({ key, item }) => (
            <ArtifactView key={key} artifact={item} />
          ))}
        </section>
      )}
    </>
  );
}

/** Nhầm lẫn của một phương án sai; câu lý thuyết luôn kèm trích dẫn nguyên văn của khái niệm (I3). */
function NoteBody({ note, titles }: { note: DistractorNote; titles: ReadonlyMap<string, string> }) {
  const caption = note.citation === null ? null : sourceCaption(note.citation, titles);
  return (
    <>
      <p className={styles.noteLabel}>
        Phương án {note.key}: {note.label}
      </p>
      <p>{note.rationale}</p>
      {note.citation !== null && caption !== null && (
        <VerbatimQuote citation={note.citation} pageLabel={caption.pageLabel} sourceTitle={caption.title} />
      )}
    </>
  );
}
