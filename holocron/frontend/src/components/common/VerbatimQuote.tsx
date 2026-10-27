import { highlightSegments } from "@/lib/text";
import type { Citation } from "@/types/holocron";

import styles from "./VerbatimQuote.module.css";

interface VerbatimQuoteProps {
  citation: Citation;
  /** Tên tài liệu nếu màn hình đã có; không có thì chỉ hiện mã tài liệu. */
  sourceTitle?: string | null;
  /**
   * Khoảng cần tô `[start, end)` theo vị trí UTF-16 trong `verbatim_quote` (ví dụ vị trí `term` của cặp khái niệm).
   * Chỉ bọc `<mark>`, không đổi chữ; khoảng sai dạng, ngoài biên hoặc chồng nhau bị bỏ. Không truyền thì như cũ.
   */
  highlights?: readonly (readonly [number, number])[];
  /** Nhãn trang thay cho "tr. {page_number}" (ví dụ "tr. 3–5" của chunk nhiều trang). Không truyền thì như cũ. */
  pageLabel?: string;
}

/**
 * Trích dẫn nguyên văn từ tài liệu nguồn (I3; kế hoạch UI mục 5.5). Hiện đúng chuỗi backend trả, không sửa, không
 * cắt; dấu ngoặc kép là CSS nên thao tác chép giữ đúng nguyên văn. Nhãn trang luôn hiện. Chưa có nút "Mở trang" vì
 * chưa có cách lấy file gốc (M12).
 */
export function VerbatimQuote({ citation, sourceTitle = null, highlights, pageLabel }: VerbatimQuoteProps) {
  const text = citation.verbatim_quote;
  return (
    <figure className={styles.quote}>
      <blockquote className={styles.text}>
        {highlights === undefined || highlights.length === 0
          ? text
          : highlightSegments(text, highlights).map((segment) =>
              segment.marked ? (
                <mark key={segment.start} className={styles.mark}>
                  {segment.text}
                </mark>
              ) : (
                <span key={segment.start}>{segment.text}</span>
              ),
            )}
      </blockquote>
      <figcaption className={styles.caption}>
        <span className={styles.page}>{pageLabel ?? `tr. ${citation.page_number}`}</span>
        <span aria-hidden="true"> · </span>
        {sourceTitle !== null && <span>{sourceTitle} </span>}
        <code className={styles.doc}>{citation.doc_id}</code>
      </figcaption>
    </figure>
  );
}
