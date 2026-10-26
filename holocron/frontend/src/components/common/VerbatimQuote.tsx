import type { Citation } from "@/types/holocron";

import styles from "./VerbatimQuote.module.css";

interface VerbatimQuoteProps {
  citation: Citation;
  /** Tên tài liệu nếu màn hình đã có; không có thì chỉ hiện mã tài liệu. */
  sourceTitle?: string | null;
}

/**
 * Trích dẫn nguyên văn từ tài liệu nguồn (I3; kế hoạch UI mục 5.5). Hiện đúng chuỗi backend trả, không sửa, không
 * cắt; dấu ngoặc kép là CSS nên thao tác chép giữ đúng nguyên văn. Nhãn trang luôn hiện. Chưa có nút "Mở trang" vì
 * chưa có cách lấy file gốc (M12).
 */
export function VerbatimQuote({ citation, sourceTitle = null }: VerbatimQuoteProps) {
  return (
    <figure className={styles.quote}>
      <blockquote className={styles.text}>{citation.verbatim_quote}</blockquote>
      <figcaption className={styles.caption}>
        <span className={styles.page}>tr. {citation.page_number}</span>
        <span aria-hidden="true"> · </span>
        {sourceTitle !== null && <span>{sourceTitle} </span>}
        <code className={styles.doc}>{citation.doc_id}</code>
      </figcaption>
    </figure>
  );
}
