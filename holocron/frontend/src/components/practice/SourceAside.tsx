"use client";

import { Icon } from "@/components/common/Icon";
import type { PracticeCitation } from "@/lib/practice-data";
import { sourceCaption } from "@/lib/quiz";

import { CitationPanel } from "./CitationPanel";
import styles from "./Quiz.module.css";

interface SourceAsideProps {
  /** Câu đang mở đã trả lời: chỉ từ lúc này mới hiện trích dẫn (với câu lý thuyết, trích dẫn chính là đáp án). */
  answered: boolean;
  citation: PracticeCitation;
  titles: ReadonlyMap<string, string>;
  subjectCode: string;
  chapterCode: string | null;
  open: boolean;
  onToggle: () => void;
}

/**
 * Cột phải 340 px "Nguồn" của màn Làm bài. Trước khi trả lời: ô viền đứt báo chỗ trích dẫn sẽ hiện. Sau khi trả lời:
 * khung trượt vào (`m-source-in`) với trích dẫn nguyên văn, "Trang N · tên tài liệu" (không có mã tài liệu) và nút
 * "Xem đoạn nguồn (C)".
 */
export function SourceAside({
  answered,
  citation,
  titles,
  subjectCode,
  chapterCode,
  open,
  onToggle,
}: SourceAsideProps) {
  if (!answered) {
    return (
      <div className={styles.sourcePlaceholder}>
        <Icon name="quote" size={20} />
        <span>Trả lời xong, đoạn trích nguyên văn và số trang sẽ hiện ở đây.</span>
      </div>
    );
  }
  const caption = sourceCaption(citation, titles);
  return (
    <aside className={`m-source-in ${styles.source}`} aria-label="Nguồn">
      <p className={styles.sourceTitle}>
        <Icon name="quote" size={20} />
        Nguồn
      </p>
      <CitationPanel
        citation={citation}
        subjectCode={subjectCode}
        chapterCode={chapterCode}
        open={open}
        onToggle={onToggle}
        pageLabel={caption.pageLabel}
        sourceTitle={caption.title}
      />
    </aside>
  );
}
