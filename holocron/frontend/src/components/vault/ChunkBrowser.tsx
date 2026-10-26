"use client";

import Link from "next/link";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useId, useRef, useState } from "react";

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
  buildLabelChange,
  CHUNK_CATEGORY_OPTIONS,
  type ChunkListView,
  type ChunkView,
  canEditLabels,
  categoryLabel,
  checkSearchText,
  groupByChapter,
  hasNextPage,
  type LabelDraft,
  type LabelField,
  labelDraftOf,
  labelStatusLabel,
  nextPageOffset,
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
 * bằng `offset`/`limit` của backend), tìm kiếm (`GET /v1/vault/search`, không hiện điểm) và sửa, xác nhận nhãn của
 * từng chunk (`PATCH /v1/vault/chunks/{chunk_id}/labels`). Văn bản chunk là văn bản người dùng đã xác nhận ở bước xem
 * lại trang, không sửa được ở đây; đoạn trích tiêu biểu được tô theo luật so khớp của I3.
 */
export function ChunkBrowser({ subjectCode }: { subjectCode: string }) {
  const { runtime } = useShell();
  const search = capabilityOf(freshRuntime(runtime), "vault_search");
  // Tăng sau mỗi lần sửa nhãn thành công: danh sách và kết quả tìm kiếm đọc lại từ backend, không vá tại chỗ.
  const [revision, setRevision] = useState(0);
  const onLabelsSaved = useCallback(() => setRevision((value) => value + 1), []);
  return (
    <>
      <VaultSearch subjectCode={subjectCode} revision={revision} onLabelsSaved={onLabelsSaved} />
      {search.state === "unavailable" && (
        <p className={styles.hint}>
          {CAPABILITY_LABEL_VI.vault_search}: {search.detail ?? "chưa dùng được"}
        </p>
      )}
      <ChunkList subjectCode={subjectCode} revision={revision} onLabelsSaved={onLabelsSaved} />
    </>
  );
}

interface ChunkSectionProps {
  subjectCode: string;
  /** Đổi sau mỗi lần sửa nhãn thành công ở bất kỳ đâu trong mục này. */
  revision: number;
  onLabelsSaved: () => void;
}

function ChunkList({ subjectCode, revision, onLabelsSaved }: ChunkSectionProps) {
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

  // Sửa nhãn thành công (ở danh sách hay ở kết quả tìm kiếm): đọc lại trang hiện tại từ backend.
  const seenRevision = useRef(revision);
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    load();
  }, [revision, load]);

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
      {state.kind === "ready" && state.value.received === 0 && (
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
      {state.kind === "ready" && state.value.received > 0 && (
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
                  <ChunkCard key={chunk.chunkId} chunk={chunk} onLabelsSaved={onLabelsSaved} onReload={load} />
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
              disabled={!hasNextPage(filter.offset, state.value.received, state.value.total)}
              onClick={() => setFilter({ ...filter, offset: nextPageOffset(filter.offset, state.value.received) })}
            >
              Trang sau
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function VaultSearch({ subjectCode, revision, onLabelsSaved }: ChunkSectionProps) {
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

  // Sửa nhãn thành công: chạy lại truy vấn gần nhất (nếu đã tìm) để kết quả là bản backend trả.
  const seenRevision = useRef(revision);
  useEffect(() => {
    if (seenRevision.current === revision) return;
    seenRevision.current = revision;
    if (lastQuery.current !== null) run(lastQuery.current);
  }, [revision, run]);

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
              <ChunkCard key={hit.chunk.chunkId} chunk={hit.chunk} onLabelsSaved={onLabelsSaved} onReload={retry} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

interface ChunkCardProps {
  chunk: ChunkView;
  /** Sửa nhãn thành công: mục cha đọc lại danh sách từ backend. */
  onLabelsSaved: () => void;
  /** Nút "Tải lại bản mới nhất" của lỗi 409 khi sửa nhãn: đọc lại danh sách chứa chunk này. */
  onReload: () => void;
}

/**
 * Một chunk: chương, trang, nhãn, và văn bản nguyên văn (đoạn trích tiêu biểu được tô nếu định vị được). Nhãn sửa và
 * xác nhận được khi backend trả `version`; văn bản và đoạn trích không có ô sửa (§6.6).
 */
function ChunkCard({ chunk, onLabelsSaved, onReload }: ChunkCardProps) {
  const segments = locateQuote(chunk.rawText, chunk.verbatimQuote);
  const [editing, setEditing] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  // Đóng form (huỷ hoặc Esc) thì trả focus về nút mở form.
  useEffect(() => {
    if (!editing && wasEditing.current) editButton.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

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
      {editing ? (
        <LabelEditor
          chunk={chunk}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onLabelsSaved();
          }}
          onReload={() => {
            setEditing(false);
            onReload();
          }}
        />
      ) : canEditLabels(chunk) ? (
        <div className={styles.actions}>
          <button
            ref={editButton}
            type="button"
            className={`${styles.secondary} ${styles.small}`}
            onClick={() => setEditing(true)}
          >
            {chunk.labelStatus === "confirmed" ? "Sửa nhãn" : "Sửa hoặc xác nhận nhãn"}
          </button>
        </div>
      ) : (
        <p className={styles.hint}>Backend không trả phiên bản của đoạn này nên chưa sửa được nhãn ở đây.</p>
      )}
    </li>
  );
}

interface LabelEditorProps {
  chunk: ChunkView;
  onCancel: () => void;
  onSaved: () => void;
  onReload: () => void;
}

/**
 * Form sửa nhãn một chunk: chương, loại nội dung, khoá dạng bài. Lưu gửi `PATCH …/labels` chỉ với field đã đổi kèm
 * `expected_version`; không đổi gì thì lưu nghĩa là xác nhận nhãn hiện tại (backend đặt `label_status = confirmed`).
 */
function LabelEditor({ chunk, onCancel, onSaved, onReload }: LabelEditorProps) {
  const ids = useId();
  const [draft, setDraft] = useState<LabelDraft>(() => labelDraftOf(chunk));
  const [errors, setErrors] = useState<Partial<Record<LabelField, string>>>({});
  const [saveError, setSaveError] = useState<UiError | null>(null);
  const [saving, setSaving] = useState(false);
  const firstInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstInput.current?.focus();
  }, []);

  async function save(): Promise<void> {
    if (saving) return;
    const check = buildLabelChange(chunk, draft);
    setErrors(check.ok ? {} : check.errors);
    if (!check.ok) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.patchChunkLabels(chunk.chunkId, check.change);
      onSaved();
    } catch (caught) {
      setSaveError(toUiError(caught));
      setSaving(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLFormElement>): void {
    if (event.key !== "Escape" || saving) return;
    event.preventDefault();
    event.stopPropagation();
    onCancel();
  }

  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      onKeyDown={onKeyDown}
      noValidate
      aria-label={`Sửa nhãn đoạn ${pageRangeLabel(chunk)}`}
    >
      <div className={styles.fields}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Chương</span>
          <input
            ref={firstInput}
            className={`${styles.input} ${styles.codeInput}`}
            value={draft.chapterCode}
            onChange={(event) => setDraft({ ...draft, chapterCode: event.target.value })}
            aria-invalid={errors.chapter !== undefined}
            aria-describedby={`${ids}-chapter`}
            spellCheck={false}
            disabled={saving}
          />
          <span id={`${ids}-chapter`} className={errors.chapter === undefined ? styles.hint : styles.fieldError}>
            {errors.chapter ?? "Dạng ch1 hoặc ch2.1."}
          </span>
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Loại nội dung</span>
          <select
            className={styles.select}
            value={draft.category}
            onChange={(event) => setDraft({ ...draft, category: event.target.value })}
            aria-invalid={errors.category !== undefined}
            disabled={saving}
          >
            {draft.category === "" && <option value="">Giữ loại hiện tại</option>}
            {CHUNK_CATEGORY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {errors.category !== undefined && <span className={styles.fieldError}>{errors.category}</span>}
        </label>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Khoá dạng bài</span>
          <textarea
            className={`${styles.input} ${styles.wideInput}`}
            rows={3}
            value={draft.topicKeysText}
            onChange={(event) => setDraft({ ...draft, topicKeysText: event.target.value })}
            aria-invalid={errors.topicKeys !== undefined}
            aria-describedby={`${ids}-topics`}
            spellCheck={false}
            disabled={saving}
          />
          <span id={`${ids}-topics`} className={errors.topicKeys === undefined ? styles.hint : styles.fieldError}>
            {errors.topicKeys ??
              "Mỗi dòng một khoá, dạng mon.chuong.dang_bai (chữ thường không dấu). Để trống nếu đoạn không thuộc dạng bài nào."}
          </span>
        </label>
      </div>
      {errors.version !== undefined && <p className={styles.fieldError}>{errors.version}</p>}
      {saveError !== null && <ErrorNotice error={saveError} onRetry={() => void save()} onReload={onReload} />}
      <div className={styles.actions}>
        <button type="submit" className={`${styles.primary} ${styles.small}`} disabled={saving}>
          {saving ? "Đang lưu…" : "Lưu và xác nhận nhãn"}
        </button>
        <button type="button" className={`${styles.secondary} ${styles.small}`} onClick={onCancel} disabled={saving}>
          Huỷ
        </button>
      </div>
      <p className={styles.hint}>
        Không đổi ô nào mà bấm lưu nghĩa là xác nhận nhãn hiện tại. Văn bản và đoạn trích không sửa được ở đây vì đã
        được xác nhận ở bước xem lại trang.
      </p>
    </form>
  );
}
