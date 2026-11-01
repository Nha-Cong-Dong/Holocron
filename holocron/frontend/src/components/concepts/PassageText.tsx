"use client";

import { useEffect, useMemo, useRef } from "react";

import { selectionRange, selectionToQuote } from "@/lib/pairs";
import { canHighlight, findQuoteRange, QUOTE_HIGHLIGHT_NAME } from "@/lib/quote-highlight";
import { chapterLabel } from "@/lib/sky";
import { type ChunkView, pageRangeLabel } from "@/lib/vault";

import styles from "./ConceptsTab.module.css";

/** Câu báo khi vùng bôi không nằm trọn trong văn bản của đoạn. */
export const SELECTION_OUTSIDE_VI = "Chỉ bôi trong văn bản của đoạn này.";
/** Câu báo khi trích dẫn của cặp không tìm thấy trong đoạn: không tô và không coi là đã kiểm chứng (I3). */
export const HIGHLIGHT_NOT_FOUND_VI =
  "Không tìm thấy đoạn trích này trong văn bản của đoạn nên chưa tô được. Chưa coi là đã kiểm chứng.";
/** Câu báo khi trình duyệt chưa tô được trên văn bản: đoạn trích hiện riêng bên dưới. */
export const HIGHLIGHT_UNSUPPORTED_VI =
  "Trình duyệt này chưa tô sáng được trên văn bản. Đoạn trích hiện riêng bên dưới.";

interface PassageTextProps {
  passage: ChunkView;
  /** Đã bôi một đoạn hợp lệ: đúng lát của `raw_text`, chỉ bỏ khoảng trắng hai đầu. */
  onSelect: (quote: string) => void;
  /** Vùng bôi tràn ra ngoài văn bản của đoạn. */
  onInvalid: (message: string) => void;
  /** Trích dẫn của cặp đang xem: tô sáng trên văn bản (không đổi DOM chữ). `null` ⇒ không tô. */
  highlightQuote?: string | null;
}

/**
 * Văn bản của một đoạn để bôi định nghĩa (khung Đoạn văn của tab Khái niệm). `raw_text` nằm trong MỘT text node
 * (`white-space: pre-wrap`), nên vị trí của `Selection` là vị trí trong `raw_text`; không tô gì bên trong để giữ một
 * text node. Thả chuột sau khi bôi (kể cả khi thả ngoài khung) thì lấy đoạn; vùng bôi không chạm văn bản thì bỏ qua.
 * Không dùng chuột thì dùng các chip chọn câu bên dưới. Chú thích chỉ có trang và chương, không có mã tài liệu.
 *
 * Tô sáng trích dẫn của một cặp bằng CSS Custom Highlight API (`CSS.highlights` + `::highlight()`): vùng tô là một
 * `Range` trên chính text node đó, không chèn `<mark>`, nên offset của `Selection` vẫn là offset trong `raw_text`.
 * Thiếu API hoặc không tìm thấy vị trí thì không tô; trích dẫn hiện riêng kèm câu giải thích, không như đã kiểm chứng.
 */
export function PassageText({ passage, onSelect, onInvalid, highlightQuote = null }: PassageTextProps) {
  const textRef = useRef<HTMLPreElement>(null);
  const rawText = passage.rawText;
  const range = useMemo(
    () => (highlightQuote === null ? null : findQuoteRange(rawText, highlightQuote)),
    [rawText, highlightQuote],
  );
  const supported = typeof window !== "undefined" && canHighlight(window);

  useEffect(() => {
    const node = textRef.current?.firstChild;
    if (range === null || !supported || !(node instanceof Text)) return;
    const domRange = new Range();
    domRange.setStart(node, range.start);
    domRange.setEnd(node, range.end);
    CSS.highlights.set(QUOTE_HIGHLIGHT_NAME, new Highlight(domRange));
    return () => {
      CSS.highlights.delete(QUOTE_HIGHLIGHT_NAME);
    };
  }, [range, supported]);

  useEffect(() => {
    function capture(event: MouseEvent): void {
      // Thả chuột trong ô nhập hay trên nút: không đọc lại vùng bôi cũ (Firefox giữ vùng bôi của trang khi gõ trong ô).
      if (event.target instanceof Element && event.target.closest("input, textarea, select, button") !== null) return;
      const element = textRef.current;
      const selection = window.getSelection();
      if (element === null || selection === null || selection.rangeCount === 0 || selection.isCollapsed) return;
      const { anchorNode, focusNode } = selection;
      const touches =
        (anchorNode !== null && element.contains(anchorNode)) || (focusNode !== null && element.contains(focusNode));
      if (!touches) return;
      const sameNode = anchorNode === focusNode && anchorNode === element.firstChild;
      const nodeData = anchorNode instanceof Text ? anchorNode.data : null;
      const range = selectionRange(rawText, nodeData, selection.anchorOffset, selection.focusOffset, sameNode);
      if (range === null) {
        onInvalid(SELECTION_OUTSIDE_VI);
        return;
      }
      const quote = selectionToQuote(rawText, range.start, range.end);
      if (quote !== "") onSelect(quote);
    }
    document.addEventListener("mouseup", capture);
    return () => document.removeEventListener("mouseup", capture);
  }, [rawText, onSelect, onInvalid]);

  return (
    <div className={styles.preview}>
      <p className={styles.meta}>
        <span className={styles.pageLabel}>{pageRangeLabel(passage)}</span>
        <span>{chapterLabel(passage.chapterCode)}</span>
      </p>
      <pre ref={textRef} className={styles.passageText}>
        {rawText}
      </pre>
      {highlightQuote !== null && (range === null || !supported) && (
        <div className={styles.quoteAside}>
          <p className={styles.unverified}>{range === null ? HIGHLIGHT_NOT_FOUND_VI : HIGHLIGHT_UNSUPPORTED_VI}</p>
          <p className={styles.plainQuote}>{highlightQuote}</p>
        </div>
      )}
    </div>
  );
}
