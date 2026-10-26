"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { VerbatimQuote } from "@/components/common/VerbatimQuote";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import type { PracticeCitation, VaultChunkView } from "@/lib/practice-data";
import { CHUNK_PAGE_LIMIT, locateQuote, nextChunkOffset } from "@/lib/source";

import styles from "./Practice.module.css";

interface CitationPanelProps {
  citation: PracticeCitation;
  subjectCode: string;
  /** Chương của câu (từ blueprint), để thu hẹp truy vấn chunk; `null` khi không biết. */
  chapterCode: string | null;
  open: boolean;
  onToggle: () => void;
}

/**
 * Trích dẫn nguyên văn kèm số trang (luôn hiện sau khi nộp), và nút mở đoạn nguồn: văn bản chunk chứa trích dẫn, đoạn
 * trích được tô đậm. Không render PDF (chưa có cách lấy file gốc, kế hoạch UI M12).
 */
export function CitationPanel({ citation, subjectCode, chapterCode, open, onToggle }: CitationPanelProps) {
  return (
    <section className={styles.source} aria-label="Trích dẫn nguồn">
      <h3 className={styles.sectionTitle}>Trích dẫn nguyên văn</h3>
      <VerbatimQuote citation={citation} />
      <div className={styles.actions}>
        <button type="button" className={styles.secondary} onClick={onToggle} aria-expanded={open}>
          {open ? "Đóng đoạn nguồn (C)" : "Xem đoạn nguồn (C)"}
        </button>
      </div>
      {open && <SourceExcerpt citation={citation} subjectCode={subjectCode} chapterCode={chapterCode} />}
    </section>
  );
}

type SourceState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "missing" }
  | { kind: "ready"; chunk: VaultChunkView };

interface SourceExcerptProps {
  citation: PracticeCitation;
  subjectCode: string;
  chapterCode: string | null;
}

/** Tìm chunk của trích dẫn qua `GET /v1/vault/chunks` (§6.6, đọc theo trang) rồi hiện văn bản với đoạn trích tô đậm. */
function SourceExcerpt({ citation, subjectCode, chapterCode }: SourceExcerptProps) {
  const chunkId = citation.chunk_id;
  const [state, setState] = useState<SourceState>({ kind: "loading" });
  const textRef = useRef<HTMLPreElement>(null);

  const load = useCallback(() => {
    if (chunkId === null) return () => undefined;
    let cancelled = false;
    setState({ kind: "loading" });
    async function find(id: string): Promise<SourceState> {
      let offset: number | null = 0;
      let pagesRead = 0;
      while (offset !== null) {
        const page = await api.listVaultChunks({ subjectCode, chapterCode, offset, limit: CHUNK_PAGE_LIMIT });
        pagesRead += 1;
        const found = page.chunks.find((chunk) => chunk.chunkId === id);
        if (found !== undefined) return { kind: "ready", chunk: found };
        offset = nextChunkOffset(offset, page.chunks.length, page.total, pagesRead);
      }
      return { kind: "missing" };
    }
    find(chunkId)
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ kind: "error", error: toUiError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [chunkId, subjectCode, chapterCode]);

  useEffect(() => load(), [load]);

  // Phím `C` mở đoạn nguồn và đưa focus tới đó (kế hoạch UI mục 5.7).
  useEffect(() => {
    if (state.kind === "ready") textRef.current?.focus();
  }, [state.kind]);

  if (chunkId === null) {
    return (
      <p className={styles.muted}>
        Trích dẫn này không gắn với đoạn nào trong kho tri thức nên chưa xem được đoạn nguồn.
      </p>
    );
  }
  if (state.kind === "loading") return <Loading label="Đang tải đoạn nguồn…" />;
  if (state.kind === "error") return <ErrorNotice error={state.error} onRetry={load} />;
  if (state.kind === "missing") {
    return (
      <p className={styles.muted}>
        Không tìm thấy đoạn nguồn trong kho tri thức của môn này (có thể tài liệu gốc đã bị xoá). Trích dẫn ở trên vẫn
        là nguyên văn.
      </p>
    );
  }
  const segments = locateQuote(state.chunk.rawText, citation.verbatim_quote);
  return (
    <div className={styles.source}>
      <p className={styles.muted}>
        Đoạn nguồn, trang {state.chunk.pageNumber}
        {segments === null && ". Không định vị được đoạn trích trong văn bản này; trích dẫn ở trên vẫn là nguyên văn."}
      </p>
      <pre ref={textRef} className={styles.sourceText} tabIndex={-1}>
        {segments === null ? (
          state.chunk.rawText
        ) : (
          <>
            {segments.before}
            <mark className={styles.highlight}>{segments.match}</mark>
            {segments.after}
          </>
        )}
      </pre>
    </div>
  );
}
