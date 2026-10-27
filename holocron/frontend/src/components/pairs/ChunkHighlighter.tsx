"use client";

import { useEffect, useRef } from "react";

import { selectionRange, selectionToQuote } from "@/lib/pairs";
import { type ChunkView, pageRangeLabel } from "@/lib/vault";

import styles from "./Pairs.module.css";

/** Câu báo khi vùng bôi không nằm trọn trong văn bản của chunk. */
export const SELECTION_OUTSIDE_VI = "Chỉ bôi trong văn bản của chunk.";

interface ChunkHighlighterProps {
  chunk: ChunkView;
  /** Đã bôi một đoạn hợp lệ: đúng lát của `raw_text`, chỉ bỏ khoảng trắng hai đầu. */
  onSelect: (quote: string) => void;
  /** Vùng bôi tràn ra ngoài văn bản chunk. */
  onInvalid: (message: string) => void;
}

/**
 * Văn bản của chunk để bôi định nghĩa. `raw_text` nằm trong MỘT text node (`white-space: pre-wrap`), nên vị trí của
 * `Selection` là vị trí trong `raw_text`; không tô gì bên trong để giữ một text node. Thả chuột sau khi bôi (kể cả
 * khi thả ngoài khung) thì lấy đoạn; vùng bôi không chạm văn bản chunk thì bỏ qua. Không dùng chuột thì dùng ô
 * "Nhập hoặc dán định nghĩa" ở form.
 */
export function ChunkHighlighter({ chunk, onSelect, onInvalid }: ChunkHighlighterProps) {
  const textRef = useRef<HTMLPreElement>(null);
  const rawText = chunk.rawText;

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
        <span className={styles.pageLabel}>{pageRangeLabel(chunk)}</span>
        <span>
          Chương <span className={styles.code}>{chunk.chapterCode}</span>
        </span>
        <code>{chunk.docId}</code>
      </p>
      <pre ref={textRef} className={styles.chunkText}>
        {rawText}
      </pre>
    </div>
  );
}
