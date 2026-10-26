"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { VAULT_BROWSE_PAGE_SIZE, VAULT_SEARCH_LIMIT } from "@/lib/limits";
import { CAPABILITY_LABEL_VI, capabilityOf, freshRuntime } from "@/lib/runtime";
import { locateQuote } from "@/lib/source";
import {
  type ChunkListView,
  type ChunkView,
  categoryLabel,
  checkSearchText,
  groupByChapter,
  hasNextPage,
  labelStatusLabel,
  pageRangeLabel,
  parseChapterFilter,
  previousOffset,
  type SearchResultView,
  vaultHref,
} from "@/lib/vault";
import type { LabelStatus } from "@/types/holocron";

import styles from "./Vault.module.css";

type Loadable<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; value: T };

interface BrowseFilter {
  chapterCode: string | null;
  labelStatus: LabelStatus | null;
  offset: number;
}

const LABEL_FILTERS: { value: LabelStatus | ""; label: string }[] = [
  { value: "", label: "Mọi trạng thái nhãn" },
  { value: "proposed", label: "Nhãn đề xuất, chưa xác nhận" },
  { value: "confirmed", label: "Nhãn đã xác nhận" },
];

/**
 * Mục "Kho tri thức" của một môn (§6.6): duyệt chunk theo chương và theo trang (`GET /v1/vault/chunks`, phân trang
 * bằng `offset`/`limit` của backend) và tìm kiếm (`GET /v1/vault/search`, không hiện điểm). Văn bản chunk là văn bản
 * người dùng đã xác nhận ở bước xem lại trang; đoạn trích tiêu biểu được tô theo luật so khớp của I3.
 */
export function ChunkBrowser({ subjectCode }: { subjectCode: string }) {
  const { runtime } = useShell();
  const search = capabilityOf(freshRuntime(runtime), "vault_search");
  return (
    <>
      <VaultSearch subjectCode={subjectCode} />
      {search.state === "unavailable" && (
        <p className={styles.hint}>
          {CAPABILITY_LABEL_VI.vault_search}: {search.detail ?? "chưa dùng được"}
        </p>
      )}
      <ChunkList subjectCode={subjectCode} />
    </>
  );
}

function ChunkList({ subjectCode }: { subjectCode: string }) {
  const ids = useId();
  const [filter, setFilter] = useState<BrowseFilter>({ chapterCode: null, labelStatus: null, offset: 0 });
  const [chapterText, setChapterText] = useState("");
  const [chapterInvalid, setChapterInvalid] = useState(false);
  const [labelValue, setLabelValue] = useState<LabelStatus | "">("");
  const [state, setState] = useState<Loadable<ChunkListView>>({ kind: "loading" });
  const calls = useRef(createLatestCounter());

  const load = useCallback(() => {
    const ticket = calls.current.begin();
    setState({ kind: "loading" });
    api
      .browseVaultChunks({ subjectCode, topicKeys: [], limit: VAULT_BROWSE_PAGE_SIZE, ...filter })
      .then((value) => {
        if (calls.current.isLatest(ticket)) setState({ kind: "ready", value });
      })
      .catch((error: unknown) => {
        if (calls.current.isLatest(ticket)) setState({ kind: "error", error: toUiError(error) });
      });
  }, [subjectCode, filter]);

  useEffect(() => {
    load();
    const counter = calls.current;
    return () => counter.invalidate();
  }, [load]);

  function applyFilter(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const chapter = parseChapterFilter(chapterText);
    setChapterInvalid(!chapter.ok);
    if (!chapter.ok) return;
    setFilter({ chapterCode: chapter.chapter, labelStatus: labelValue === "" ? null : labelValue, offset: 0 });
  }

  const filtered = filter.chapterCode !== null || filter.labelStatus !== null;

  return (
    <section className={styles.panel} aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>Văn bản trong kho tri thức</h2>
      <form className={styles.actions} onSubmit={applyFilter} noValidate>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Chương</span>
          <input
            className={`${styles.input} ${styles.codeInput}`}
            value={chapterText}
            onChange={(event) => setChapterText(event.target.value)}
            placeholder="mọi chương"
            aria-invalid={chapterInvalid}
            aria-describedby={`${ids}-chapter-hint`}
            spellCheck={false}
          />
          <span id={`${ids}-chapter-hint`} className={chapterInvalid ? styles.fieldError : styles.hint}>
            {chapterInvalid ? "Mã chương có dạng ch1 hoặc ch2.1." : "Bỏ trống để xem mọi chương."}
          </span>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Nhãn</span>
          <select
            className={styles.select}
            value={labelValue}
            onChange={(event) => {
              const next = LABEL_FILTERS.find((item) => item.value === event.target.value);
              setLabelValue(next?.value ?? "");
            }}
          >
            {LABEL_FILTERS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className={styles.secondary}>
          Lọc
        </button>
      </form>

      {state.kind === "loading" && <Loading label="Đang tải văn bản trong kho tri thức…" />}
      {state.kind === "error" && <ErrorNotice error={state.error} onRetry={load} onReload={load} />}
      {state.kind === "ready" && state.value.chunks.length === 0 && (
        <EmptyState title={filtered ? "Không có đoạn văn bản nào khớp bộ lọc." : "Kho tri thức của môn này còn trống."}>
          {filtered ? (
            "Bỏ bớt bộ lọc để xem các đoạn khác."
          ) : (
            <>
              Văn bản vào kho sau khi bạn xác nhận mọi trang của một tài liệu.{" "}
              <Link href={vaultHref({ subject: subjectCode })}>Mở mục Tài liệu</Link> để nhập PDF hoặc xác nhận trang.
            </>
          )}
        </EmptyState>
      )}
      {state.kind === "ready" && state.value.chunks.length > 0 && (
        <>
          <p className={styles.muted}>Tổng {state.value.total} đoạn văn bản khớp bộ lọc.</p>
          {state.value.hasSkipped && (
            <p className={styles.warningNote}>Có đoạn backend trả về sai dạng nên không hiển thị.</p>
          )}
          {groupByChapter(state.value.chunks).map((group) => (
            <section key={group.chapterCode} className={styles.chapter} aria-label={`Chương ${group.chapterCode}`}>
              <h3>
                Chương <span className={styles.code}>{group.chapterCode}</span>
              </h3>
              <ul className={styles.chunkList}>
                {group.chunks.map((chunk) => (
                  <ChunkCard key={chunk.chunkId} chunk={chunk} />
                ))}
              </ul>
            </section>
          ))}
          <div className={styles.pager}>
            <button
              type="button"
              className={`${styles.secondary} ${styles.small}`}
              disabled={filter.offset === 0}
              onClick={() => setFilter({ ...filter, offset: previousOffset(filter.offset, VAULT_BROWSE_PAGE_SIZE) })}
            >
              Trang trước
            </button>
            <button
              type="button"
              className={`${styles.secondary} ${styles.small}`}
              disabled={!hasNextPage(filter.offset, state.value.chunks.length, state.value.total)}
              onClick={() => setFilter({ ...filter, offset: filter.offset + state.value.chunks.length })}
            >
              Trang sau
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function VaultSearch({ subjectCode }: { subjectCode: string }) {
  const ids = useId();
  const [text, setText] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);
  const [state, setState] = useState<Loadable<SearchResultView>>({ kind: "idle" });
  const calls = useRef(createLatestCounter());
  const lastQuery = useRef<string | null>(null);

  useEffect(() => {
    const counter = calls.current;
    return () => counter.invalidate();
  }, []);

  const run = useCallback(
    (q: string) => {
      lastQuery.current = q;
      const ticket = calls.current.begin();
      setState({ kind: "loading" });
      api
        .searchVault({ q, subjectCode, topicKeys: [], limit: VAULT_SEARCH_LIMIT })
        .then((value) => {
          if (calls.current.isLatest(ticket)) setState({ kind: "ready", value });
        })
        .catch((error: unknown) => {
          if (calls.current.isLatest(ticket)) setState({ kind: "error", error: toUiError(error) });
        });
    },
    [subjectCode],
  );

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const check = checkSearchText(text);
    setInputError(check.ok ? null : check.message);
    if (check.ok) run(check.q);
  }

  const retry = () => {
    if (lastQuery.current !== null) run(lastQuery.current);
  };

  return (
    <section className={styles.panel} aria-labelledby={`${ids}-title`}>
      <h2 id={`${ids}-title`}>Tìm trong kho tri thức</h2>
      <form className={styles.actions} onSubmit={submit} noValidate>
        <label className={styles.field}>
          <span className="visually-hidden">Từ cần tìm</span>
          <input
            type="search"
            className={`${styles.input} ${styles.wideInput}`}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Từ hoặc cụm từ trong tài liệu"
            aria-invalid={inputError !== null}
            aria-describedby={`${ids}-hint`}
          />
          <span id={`${ids}-hint`} className={inputError === null ? styles.hint : styles.fieldError}>
            {inputError ??
              "Tìm theo âm tiết, có phân biệt dấu; không có kết quả đúng dấu thì hệ thống tự tìm khi bỏ dấu."}
          </span>
        </label>
        <button type="submit" className={styles.primary}>
          Tìm
        </button>
      </form>
      {state.kind === "loading" && <Loading label="Đang tìm…" />}
      {state.kind === "error" && <ErrorNotice error={state.error} onRetry={retry} onReload={retry} />}
      {state.kind === "ready" && state.value.hits.length === 0 && (
        <EmptyState title="Không tìm thấy đoạn nào.">
          Thử từ khác, hoặc kiểm tra tài liệu đã được xác nhận chưa.
        </EmptyState>
      )}
      {state.kind === "ready" && state.value.hits.length > 0 && (
        <>
          {state.value.hits.some((hit) => hit.index === "no_diacritics") && (
            <p className={styles.note}>Không có kết quả khớp đúng dấu; dưới đây là kết quả khớp khi bỏ dấu.</p>
          )}
          {state.value.hasSkipped && (
            <p className={styles.warningNote}>Có kết quả backend trả về sai dạng nên không hiển thị.</p>
          )}
          <ul className={styles.chunkList} aria-label="Kết quả tìm kiếm">
            {state.value.hits.map((hit) => (
              <ChunkCard key={hit.chunk.chunkId} chunk={hit.chunk} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/** Một chunk: chương, trang, nhãn, và văn bản nguyên văn (đoạn trích tiêu biểu được tô nếu định vị được). */
function ChunkCard({ chunk }: { chunk: ChunkView }) {
  const segments = locateQuote(chunk.rawText, chunk.verbatimQuote);
  return (
    <li className={styles.chunk}>
      <p className={styles.meta}>
        <span className={styles.pageLabel}>{pageRangeLabel(chunk)}</span>
        <span>
          Chương <span className={styles.code}>{chunk.chapterCode}</span>
        </span>
        <span>{categoryLabel(chunk.category)}</span>
        <span className={styles.badge} data-tone={chunk.labelStatus === "confirmed" ? "success" : "warning"}>
          {labelStatusLabel(chunk.labelStatus)}
        </span>
        <code>{chunk.docId}</code>
      </p>
      <pre className={styles.chunkText}>
        {segments === null ? (
          chunk.rawText
        ) : (
          <>
            {segments.before}
            <mark>{segments.match}</mark>
            {segments.after}
          </>
        )}
      </pre>
      {chunk.topicKeys.length > 0 && (
        <p className={styles.hint}>
          Dạng bài: <span className={styles.code}>{chunk.topicKeys.join(", ")}</span>
        </p>
      )}
    </li>
  );
}
