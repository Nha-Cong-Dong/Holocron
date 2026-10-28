"use client";

import { type FormEvent, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { ChunkHighlighter } from "@/components/pairs/ChunkHighlighter";
import { Button, Dropdown, Field, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { screenErrorText } from "@/lib/concept-board";
import { toUiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { VAULT_SEARCH_LIMIT } from "@/lib/limits";
import { type ChunkMapStatus, chaptersOf, lookupChunk, runLatest } from "@/lib/pairs";
import {
  chunksOfChapter,
  citationCaption,
  conceptCreateRequest,
  type PassageSelection,
  passageNav,
  type SentenceRange,
  selectionOfQuote,
  sentenceLabel,
  sentenceRanges,
} from "@/lib/passage";
import { chapterLabel } from "@/lib/sky";
import { type ChunkView, checkSearchText } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import styles from "./ConceptsTab.module.css";
import { SentenceChips } from "./SentenceChips";

/** Số ký tự đầu của đoạn hiện trong một dòng kết quả tìm kiếm. */
const RESULT_PREVIEW_CHARS = 90;

interface PassagePanelProps {
  subjectCode: string;
  mapStatus: ChunkMapStatus | "loading";
  map: ReadonlyMap<string, ChunkView>;
  /** Đoạn đang mở (`?chunk=`); `null` ⇒ đoạn đầu tiên của môn. */
  openChunkId: string | null;
  onOpenChunk: (chunkId: string) => void;
  onRetryMap: () => void;
  /** Khả năng tìm trong tài liệu: không có thì ẩn ô tìm kèm một câu. */
  searchAvailable: boolean;
  onCreated: (pair: ConceptPair) => void;
}

/**
 * Khung "Tự tạo khái niệm": chọn chương, đi tới đoạn trước/sau hoặc tìm trong tài liệu, rồi bôi một đoạn hoặc bấm chip
 * một câu để mở khung "Tạo khái niệm". Văn bản đoạn vẽ bằng `ChunkHighlighter` (một text node, không chèn đánh dấu).
 */
export function PassagePanel({
  subjectCode,
  mapStatus,
  map,
  openChunkId,
  onOpenChunk,
  onRetryMap,
  searchAvailable,
  onCreated,
}: PassagePanelProps) {
  return (
    <section className={styles.panel} aria-labelledby="concepts-create-title">
      <div>
        <h2 id="concepts-create-title" className={styles.h2}>
          Tự tạo khái niệm
        </h2>
        <p className={styles.muted}>Bôi một đoạn bên dưới, hoặc bấm chọn một câu, rồi bấm “Tạo khái niệm”.</p>
      </div>
      <PanelBody
        subjectCode={subjectCode}
        mapStatus={mapStatus}
        map={map}
        openChunkId={openChunkId}
        onOpenChunk={onOpenChunk}
        onRetryMap={onRetryMap}
        searchAvailable={searchAvailable}
        onCreated={onCreated}
      />
    </section>
  );
}

function PanelBody({
  subjectCode,
  mapStatus,
  map,
  openChunkId,
  onOpenChunk,
  onRetryMap,
  searchAvailable,
  onCreated,
}: PassagePanelProps) {
  if (mapStatus === "loading") {
    return (
      <div className={styles.skeletonCol} aria-busy="true">
        <Skeleton width="40%" height={18} />
        <Skeleton height={120} />
        <p className={styles.muted}>Đang tải đoạn văn của tài liệu…</p>
      </div>
    );
  }
  if (mapStatus === "error") {
    return (
      <div className={styles.skeletonCol}>
        <p className={styles.error}>
          Chưa tải được đoạn văn của tài liệu nên chưa tự tạo khái niệm được. Bấm Thử lại; nếu vẫn chưa được, mở lại
          Holocron.
        </p>
        <div>
          <Button variant="secondary" onClick={onRetryMap}>
            Thử lại
          </Button>
        </div>
      </div>
    );
  }
  if (map.size === 0) {
    return <p className={styles.muted}>Môn này chưa có đoạn văn nào. Thêm tài liệu ở tab Tài liệu rồi quay lại đây.</p>;
  }
  const lookup = openChunkId === null ? null : lookupChunk(mapStatus, map, openChunkId);
  const first = map.values().next().value;
  const current = lookup?.kind === "found" ? lookup.chunk : (first ?? null);
  if (current === null) return null;
  return (
    <>
      {lookup !== null && lookup.kind !== "found" && (
        <p className={styles.note}>
          {lookup.kind === "not_loaded"
            ? "Chưa tải hết đoạn văn của môn nên chưa mở được đoạn này. Chọn chương hoặc tìm trong tài liệu."
            : "Không tìm thấy đoạn này trong tài liệu của môn (tài liệu có thể đã bị xoá). Đang mở đoạn đầu tiên."}
        </p>
      )}
      <PassageNavigator map={map} current={current} onOpenChunk={onOpenChunk} />
      {searchAvailable ? (
        <PassageSearch subjectCode={subjectCode} onOpenChunk={onOpenChunk} />
      ) : (
        <p className={styles.muted}>
          Tìm trong tài liệu chưa dùng được trên máy này; chọn chương rồi đi qua từng đoạn.
        </p>
      )}
      <PassageEditor key={current.chunkId} chunk={current} onCreated={onCreated} />
    </>
  );
}

function PassageNavigator({
  map,
  current,
  onOpenChunk,
}: {
  map: ReadonlyMap<string, ChunkView>;
  current: ChunkView;
  onOpenChunk: (chunkId: string) => void;
}) {
  const chapters = useMemo(() => chaptersOf(map), [map]);
  const options = useMemo(() => chapters.map((code) => ({ value: code, label: chapterLabel(code) })), [chapters]);
  const nav = passageNav(chunksOfChapter(map, current.chapterCode), current.chunkId);
  return (
    <div className={styles.controls}>
      <Dropdown
        label="Chương"
        options={options}
        value={current.chapterCode}
        onChange={(code) => {
          const target = chunksOfChapter(map, code)[0];
          if (target !== undefined) onOpenChunk(target.chunkId);
        }}
      />
      {nav !== null && (
        <div className={styles.nav}>
          <Button
            variant="secondary"
            aria-disabled={nav.previous === null}
            onClick={() => {
              if (nav.previous !== null) onOpenChunk(nav.previous);
            }}
          >
            Đoạn trước
          </Button>
          <span className={styles.position}>
            Đoạn {nav.position}/{nav.total}
          </span>
          <Button
            variant="secondary"
            aria-disabled={nav.next === null}
            onClick={() => {
              if (nav.next !== null) onOpenChunk(nav.next);
            }}
          >
            Đoạn sau
          </Button>
        </div>
      )}
    </div>
  );
}

type SearchState =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "done"; hits: ChunkView[] }
  | { kind: "failed"; message: string };

function PassageSearch({ subjectCode, onOpenChunk }: { subjectCode: string; onOpenChunk: (chunkId: string) => void }) {
  const [text, setText] = useState("");
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);
  const [state, setState] = useState<SearchState>({ kind: "idle" });
  const calls = useRef(createLatestCounter());

  useEffect(() => {
    const counter = calls.current;
    return () => counter.invalidate();
  }, []);

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const check = checkSearchText(text);
    if (!check.ok) {
      setFieldError(check.message);
      return;
    }
    setFieldError(undefined);
    setState({ kind: "searching" });
    void runLatest(
      calls.current,
      () => api.searchVault({ q: check.q, subjectCode, topicKeys: [], limit: VAULT_SEARCH_LIMIT }),
      (outcome) =>
        setState(
          outcome.ok
            ? { kind: "done", hits: outcome.value.hits.map((hit) => hit.chunk) }
            : { kind: "failed", message: screenErrorText(outcome.error) },
        ),
    );
  }

  return (
    <div className={styles.skeletonCol}>
      <form className={styles.search} onSubmit={submit} aria-label="Tìm trong tài liệu">
        <Field
          className={styles.searchField}
          label="Tìm trong tài liệu"
          value={text}
          onChange={(event) => setText(event.target.value)}
          error={fieldError}
        />
        <Button variant="secondary" type="submit" busy={state.kind === "searching"} busyLabel="Đang tìm">
          Tìm
        </Button>
      </form>
      <div aria-live="polite">
        {state.kind === "failed" && <p className={styles.error}>{state.message}</p>}
        {state.kind === "done" && state.hits.length === 0 && (
          <p className={styles.muted}>Không có đoạn nào chứa từ này. Thử từ khác hoặc chọn chương ở trên.</p>
        )}
        {state.kind === "done" && state.hits.length > 0 && (
          <ul className={styles.results} aria-label="Kết quả tìm">
            {state.hits.map((chunk) => (
              <li key={chunk.chunkId}>
                <button type="button" className={styles.result} onClick={() => onOpenChunk(chunk.chunkId)}>
                  <span className={styles.resultMeta}>{citationCaption(chunk.chapterCode, chunk)}</span>
                  <span>{sentenceLabel(chunk.rawText, RESULT_PREVIEW_CHARS)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Văn bản một đoạn, chip câu và khung "Tạo khái niệm" (dựng lại khi đổi đoạn). */
function PassageEditor({ chunk, onCreated }: { chunk: ChunkView; onCreated: (pair: ConceptPair) => void }) {
  const toast = useToast();
  const ids = useId();
  const sentences = useMemo(() => sentenceRanges(chunk.rawText), [chunk.rawText]);
  const [selection, setSelection] = useState<PassageSelection | null>(null);
  const [selectNote, setSelectNote] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const [termError, setTermError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const onSelect = useCallback(
    (quote: string) => {
      const next = selectionOfQuote(chunk.rawText, quote);
      setSelection(next);
      setSelectNote(null);
      setFormError(null);
    },
    [chunk.rawText],
  );
  const onInvalid = useCallback(() => setSelectNote("Chỉ bôi trong văn bản của đoạn này."), []);

  function pick(sentence: SentenceRange | null): void {
    setSelection(sentence === null ? null : { start: sentence.start, end: sentence.end });
    setSelectNote(null);
    setFormError(null);
  }

  function clear(): void {
    setSelection(null);
    setTerm("");
    setTermError(undefined);
    setFormError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (selection === null || pending) return;
    const result = conceptCreateRequest(chunk, selection, term);
    if (!result.ok) {
      if (result.field === "term") setTermError(result.message);
      else setFormError(result.message);
      return;
    }
    setTermError(undefined);
    setFormError(null);
    setPending(true);
    try {
      const { chunkId, term: name, definitionQuote } = result.request;
      const pair = await api.createConceptPair(chunkId, name, definitionQuote);
      setPending(false);
      clear();
      toast.show(`Đã tạo khái niệm “${pair.term}”`);
      onCreated(pair);
    } catch (error) {
      setPending(false);
      setFormError(screenErrorText(toUiError(error)));
    }
  }

  const quote = selection === null ? null : chunk.rawText.slice(selection.start, selection.end);

  return (
    <>
      <div className={styles.passage}>
        <ChunkHighlighter chunk={chunk} onSelect={onSelect} onInvalid={onInvalid} />
      </div>
      {selectNote !== null && <p className={styles.error}>{selectNote}</p>}
      <SentenceChips sentences={sentences} selection={selection} onPick={pick} />
      {quote !== null && (
        <form onSubmit={(event) => void submit(event)} noValidate>
          <fieldset className={styles.popover} aria-label="Đoạn đang chọn">
            <p id={`${ids}-quote`} className={styles.selectedQuote}>
              “{quote}”
            </p>
            <Field
              label="Tên khái niệm"
              required
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              error={termError}
              hint="Tên ngắn gọn cho đoạn đang chọn."
            />
            {formError !== null && (
              <p className={styles.error} role="alert">
                {formError}
              </p>
            )}
            <div className={styles.popoverActions}>
              <Button variant="secondary" type="submit" busy={pending} busyLabel="Đang tạo">
                Tạo khái niệm
              </Button>
              <button type="button" className={styles.textButton} onClick={clear}>
                Bỏ chọn
              </button>
            </div>
          </fieldset>
        </form>
      )}
    </>
  );
}
