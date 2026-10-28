"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { api } from "@/lib/api";
import { createLatestCounter } from "@/lib/latest";
import { PAIRS_CHUNK_MAX_REQUESTS, PAIRS_CHUNK_PAGE_SIZE } from "@/lib/limits";
import {
  type ChunkMapResult,
  chaptersOf,
  INITIAL_SUBJECT_ENTRY,
  loadChunkMap,
  lookupChunk,
  PAIR_STATUS_FILTERS,
  type PairsLocation,
  pairsHref,
  parsePairsLocation,
  runLatest,
  type SubjectEntryState,
  statusAfterCreate,
  subjectEntryStep,
} from "@/lib/pairs";
import { type ChunkView, parseChapterFilter, vaultHref } from "@/lib/vault";
import type { ConceptPair, ConceptPairStatus } from "@/types/holocron";

import { ChunkPicker } from "./ChunkPicker";
import { PAIR_CARD_ATTR, PAIR_EDIT_ATTR } from "./PairCard";
import { PairEditor } from "./PairEditor";
import { PairList, type PairListState } from "./PairList";
import styles from "./Pairs.module.css";

type MapState = { status: "loading" } | ChunkMapResult;

const EMPTY_MAP: ReadonlyMap<string, ChunkView> = new Map();

/** Phần tử mang `data-*` = `pairId` (thẻ hoặc nút "Sửa" của một cặp). */
function pairElement(attribute: string, pairId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[${attribute}="${CSS.escape(pairId)}"]`);
}

/**
 * `/pairs`: xem cặp khái niệm theo chương và trạng thái, bôi một đoạn trong chunk để tạo cặp, sửa rồi xác nhận hoặc
 * từ chối (kế hoạch UI mục F3; spec §6.6). Tham số đi qua query string để build được ở chế độ static export: `?chapter=`,
 * `?status=`, `?chunk=` (chunk đang mở). Phải nằm trong `<Suspense>` vì đọc `useSearchParams()`.
 *
 * Route cũ `/pairs`: môn lấy từ khung (môn mở gần nhất); `?subject=` chỉ dùng khi vào trang lần đầu để chọn môn đó một
 * lần. Trong trang môn (`/subject?tab=concepts`): môn truyền qua prop `subjectCode`, địa chỉ nội bộ dựng bằng `hrefFor`.
 * Sau mỗi thao tác với cặp, danh sách được đọc lại từ backend (không vá tại chỗ); map chunk chỉ tải lại khi đổi môn
 * hoặc bấm "Tải lại".
 */
export function PairsView({ subjectCode: fixedSubject, hrefFor = pairsHref }: PairsViewProps) {
  const location = parsePairsLocation(useSearchParams());
  const router = useRouter();
  const { subjectCode, selectSubject } = useShell();
  const [editingId, setEditingId] = useState<string | null>(null);

  // Route cũ: `?subject=` chỉ đọc một lần khi vào trang; sau đó môn của khung đổi thì bỏ chương và chunk của môn cũ.
  // Bước đồng bộ idempotent (`subjectEntryStep`) nên effect chạy lại (React StrictMode) không làm mất
  // `?chapter=`/`?chunk=`. Môn truyền qua prop thì không đồng bộ gì.
  const entry = useRef<SubjectEntryState>(INITIAL_SUBJECT_ENTRY);
  const [entryDone, setEntryDone] = useState(false);
  const locationSubject = location.subject;
  const locationStatus = location.status;
  useEffect(() => {
    if (fixedSubject !== undefined) return;
    const step = subjectEntryStep(entry.current, locationSubject, subjectCode);
    entry.current = step.state;
    if (step.action.kind === "select") selectSubject(step.action.subject);
    if (step.action.kind === "reset") {
      setEditingId(null);
      router.replace(hrefFor({ subject: subjectCode, status: locationStatus }));
    }
    setEntryDone(step.ready);
  }, [fixedSubject, hrefFor, subjectCode, selectSubject, router, locationSubject, locationStatus]);

  const subject = fixedSubject ?? (entryDone ? subjectCode : null);

  if (fixedSubject === undefined && !entryDone) return <Loading />;
  if (subject === null) {
    return (
      <div className={styles.page}>
        <EmptyState title="Chưa chọn môn.">
          Mở một môn ở <Link href="/">trang chủ</Link>, hoặc <Link href={vaultHref({})}>mở Vault</Link> để tạo môn và
          nhập tài liệu.
        </EmptyState>
      </div>
    );
  }
  return (
    <PairsWorkspace
      key={subject}
      subject={subject}
      location={location}
      hrefFor={hrefFor}
      editingId={editingId}
      setEditingId={setEditingId}
    />
  );
}

export interface PairsViewProps {
  /** Môn cố định (trang môn); bỏ trống ⇒ môn của khung (route cũ `/pairs`). */
  subjectCode?: string;
  /** Dựng địa chỉ cho bộ lọc và đoạn đang mở; mặc định `pairsHref` (`/pairs?…`). */
  hrefFor?: (location: Partial<PairsLocation>) => string;
}

interface PairsWorkspaceProps {
  subject: string;
  location: PairsLocation;
  hrefFor: (location: Partial<PairsLocation>) => string;
  editingId: string | null;
  setEditingId: (pairId: string | null) => void;
}

/** Màn của một môn: tải map chunk và danh sách cặp; đổi môn thì dựng lại từ đầu (khoá theo môn). */
function PairsWorkspace({ subject, location, hrefFor, editingId, setEditingId }: PairsWorkspaceProps) {
  const router = useRouter();
  const chapter = location.chapter;
  const status = location.status;

  const [mapState, setMapState] = useState<MapState>({ status: "loading" });
  const mapCalls = useRef(createLatestCounter());
  const loadMap = useCallback(() => {
    setMapState({ status: "loading" });
    void runLatest(
      mapCalls.current,
      () =>
        loadChunkMap(
          (offset, limit) =>
            api.browseVaultChunks({
              subjectCode: subject,
              chapterCode: null,
              labelStatus: null,
              topicKeys: [],
              offset,
              limit,
            }),
          PAIRS_CHUNK_PAGE_SIZE,
          PAIRS_CHUNK_MAX_REQUESTS,
        ),
      (outcome) => setMapState(outcome.ok ? outcome.value : { status: "error", map: new Map(), error: outcome.error }),
    );
  }, [subject]);

  useEffect(() => {
    loadMap();
    const counter = mapCalls.current;
    return () => counter.invalidate();
  }, [loadMap]);

  const [listState, setListState] = useState<PairListState>({ kind: "loading" });
  const listCalls = useRef(createLatestCounter());
  const loadPairs = useCallback(
    (keepCurrent: boolean) => {
      if (!keepCurrent) setListState({ kind: "loading" });
      void runLatest(
        listCalls.current,
        () => api.listConceptPairs({ subjectCode: subject, chapterCode: chapter, status }),
        (outcome) =>
          setListState(outcome.ok ? { kind: "ready", value: outcome.value } : { kind: "error", error: outcome.error }),
      );
    },
    [subject, chapter, status],
  );

  useEffect(() => {
    loadPairs(false);
    const counter = listCalls.current;
    return () => counter.invalidate();
  }, [loadPairs]);

  const reloadList = useCallback(() => loadPairs(true), [loadPairs]);
  const reloadAll = useCallback(() => {
    loadMap();
    loadPairs(false);
  }, [loadMap, loadPairs]);

  // Giữ focus theo `pair_id` sau khi danh sách được đọc lại; cặp không còn trong danh sách thì về tiêu đề danh sách.
  const focusTarget = useRef<string | null>(null);
  const listHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (listState.kind !== "ready" || focusTarget.current === null) return;
    const pairId = focusTarget.current;
    focusTarget.current = null;
    (pairElement(PAIR_CARD_ATTR, pairId) ?? listHeading.current)?.focus();
  }, [listState]);

  // Huỷ sửa: trả focus về nút "Sửa" của cặp vừa sửa (nút hiện lại sau khi khung sửa đóng).
  const focusEditOf = useRef<string | null>(null);
  useEffect(() => {
    if (editingId !== null || focusEditOf.current === null) return;
    const pairId = focusEditOf.current;
    focusEditOf.current = null;
    pairElement(PAIR_EDIT_ATTR, pairId)?.focus();
  }, [editingId]);

  const pairs = listState.kind === "ready" ? listState.value.pairs : [];
  const editingPair = pairs.find((pair) => pair.pair_id === editingId) ?? null;
  const openChunk = location.chunk;

  // Người dùng mở chunk khác, hoặc cặp đang sửa không còn trong danh sách đã tải: thôi sửa. (Bấm "Sửa" đặt cặp trước
  // rồi mới đổi địa chỉ, nên chỉ xét khi chunk đang mở thực sự đổi.)
  const lastOpenChunk = useRef(openChunk);
  useEffect(() => {
    const changed = lastOpenChunk.current !== openChunk;
    lastOpenChunk.current = openChunk;
    if (editingId === null) return;
    const movedAway = changed && editingPair !== null && editingPair.chunk_id !== openChunk;
    const vanished = listState.kind === "ready" && editingPair === null;
    if (movedAway || vanished) setEditingId(null);
  }, [editingId, editingPair, openChunk, listState.kind, setEditingId]);

  function href(next: Partial<PairsLocation>): string {
    return hrefFor({ subject, chapter, status, chunk: openChunk, ...next });
  }

  const onEdit = (pair: ConceptPair) => {
    setEditingId(pair.pair_id);
    if (openChunk !== pair.chunk_id) router.push(href({ chunk: pair.chunk_id }), { scroll: false });
  };

  const onCancelEdit = () => {
    if (editingId !== null) focusEditOf.current = editingId;
    setEditingId(null);
  };

  const onChanged = (pairId: string) => {
    focusTarget.current = pairId;
    reloadList();
  };

  const [createKey, setCreateKey] = useState(0);
  const onSaved = (pair: ConceptPair) => {
    focusTarget.current = pair.pair_id;
    if (editingId !== null) {
      setEditingId(null);
      reloadList();
      return;
    }
    setCreateKey((value) => value + 1);
    const nextStatus = statusAfterCreate(status);
    if (nextStatus !== status) router.replace(href({ status: nextStatus }), { scroll: false });
    else reloadList();
  };

  const map = mapState.status === "loading" ? EMPTY_MAP : mapState.map;
  const openLookup = openChunk === null ? null : lookupChunk(mapState.status, map, openChunk);

  return (
    <div className={styles.page}>
      <p className={styles.intro}>
        Mỗi cặp gồm một thuật ngữ và đoạn định nghĩa trích nguyên văn từ văn bản trong Vault. Chỉ cặp đã xác nhận được
        dùng để sinh câu hỏi lý thuyết.
      </p>
      <FilterForm
        key={`${chapter ?? ""}|${status ?? ""}`}
        chapter={chapter}
        status={status}
        chapterSuggestions={chaptersOf(map)}
        onApply={(nextChapter, nextStatus) => router.push(href({ chapter: nextChapter, status: nextStatus }))}
        onReload={reloadAll}
      />
      {mapState.status === "partial" && (
        <p className={styles.warningNote}>
          Môn này có nhiều chunk hơn số giao diện tải được một lần. Cặp có chunk chưa tải sẽ chưa kiểm chứng được trang
          và chưa sửa, xác nhận được.
        </p>
      )}
      <div className={styles.columns}>
        <PairList
          subjectCode={subject}
          state={listState}
          filtered={chapter !== null || status !== null}
          mapStatus={mapState.status}
          map={map}
          editingPairId={editingPair?.pair_id ?? null}
          headingRef={listHeading}
          onEdit={onEdit}
          onChanged={onChanged}
          onReloadList={reloadList}
          onReloadAll={reloadAll}
        />
        <section className={styles.panel} aria-labelledby="pairs-chunk-title">
          <div className={styles.panelHeader}>
            <h2 id="pairs-chunk-title">{openChunk === null ? "Chọn chunk" : "Văn bản của chunk"}</h2>
            {openChunk !== null && (
              <Link className={styles.secondary} href={href({ chunk: null })} scroll={false}>
                Đóng chunk
              </Link>
            )}
          </div>
          {mapState.status === "loading" && <Loading label="Đang tải chunk của môn…" />}
          {mapState.status === "error" && (
            <>
              <ErrorNotice error={{ ...mapState.error, action: "none" }} />
              <div className={styles.actions}>
                <button type="button" className={styles.secondary} onClick={reloadAll}>
                  Tải lại
                </button>
              </div>
            </>
          )}
          {(mapState.status === "complete" || mapState.status === "partial") && openChunk === null && (
            <ChunkPicker
              location={location}
              subjectCode={subject}
              hrefFor={hrefFor}
              map={mapState.map}
              partial={mapState.status === "partial"}
            />
          )}
          {openLookup?.kind === "found" &&
            (editingPair !== null && editingPair.chunk_id === openLookup.chunk.chunkId ? (
              <PairEditor
                key={`edit-${editingPair.pair_id}-${editingPair.version}`}
                target={{ mode: "edit", chunk: openLookup.chunk, pair: editingPair }}
                onSaved={onSaved}
                onCancel={onCancelEdit}
                onReloadList={reloadList}
                onReloadAll={reloadAll}
              />
            ) : (
              <PairEditor
                key={`create-${openLookup.chunk.chunkId}-${createKey}`}
                target={{ mode: "create", chunk: openLookup.chunk }}
                onSaved={onSaved}
                onCancel={onCancelEdit}
                onReloadList={reloadList}
                onReloadAll={reloadAll}
              />
            ))}
          {openLookup?.kind === "missing" && (
            <p className={styles.warningNote}>
              Không tìm thấy chunk nguồn này trong kho tri thức của môn đang chọn (tài liệu có thể đã bị xoá, hoặc chunk
              thuộc môn khác). Chọn chunk khác để tạo cặp.
            </p>
          )}
          {openLookup?.kind === "not_loaded" && (
            <p className={styles.warningNote}>
              Chưa tải hết chunk của môn nên chưa mở được chunk này. Chọn chương để thu hẹp, hoặc chọn chunk khác.
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

interface FilterFormProps {
  chapter: string | null;
  status: ConceptPairStatus | null;
  chapterSuggestions: readonly string[];
  onApply: (chapter: string | null, status: ConceptPairStatus | null) => void;
  onReload: () => void;
}

/** Lọc theo chương (gợi ý từ chương có chunk) và trạng thái; áp dụng khi bấm "Lọc". */
function FilterForm({ chapter, status, chapterSuggestions, onApply, onReload }: FilterFormProps) {
  const ids = useId();
  const [chapterText, setChapterText] = useState(chapter ?? "");
  const [statusValue, setStatusValue] = useState<ConceptPairStatus | "">(status ?? "");
  const [chapterInvalid, setChapterInvalid] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const parsed = parseChapterFilter(chapterText);
    setChapterInvalid(!parsed.ok);
    if (!parsed.ok) return;
    onApply(parsed.chapter, statusValue === "" ? null : statusValue);
  }

  return (
    <form className={styles.filters} onSubmit={submit} noValidate aria-label="Lọc cặp khái niệm">
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Chương</span>
        <input
          className={`${styles.input} ${styles.codeInput}`}
          value={chapterText}
          onChange={(event) => setChapterText(event.target.value)}
          placeholder="mọi chương"
          list={`${ids}-chapters`}
          aria-invalid={chapterInvalid}
          aria-describedby={`${ids}-chapter-hint`}
          spellCheck={false}
        />
        <datalist id={`${ids}-chapters`}>
          {chapterSuggestions.map((code) => (
            <option key={code} value={code} />
          ))}
        </datalist>
        <span id={`${ids}-chapter-hint`} className={chapterInvalid ? styles.fieldError : styles.hint}>
          {chapterInvalid ? "Mã chương có dạng ch1 hoặc ch2.1." : "Bỏ trống để xem mọi chương."}
        </span>
      </label>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>Trạng thái</span>
        <select
          className={styles.select}
          value={statusValue}
          onChange={(event) => {
            const next = PAIR_STATUS_FILTERS.find((item) => item.value === event.target.value);
            setStatusValue(next?.value ?? "");
          }}
        >
          {PAIR_STATUS_FILTERS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <div className={styles.actions}>
        <button type="submit" className={styles.primary}>
          Lọc
        </button>
        <button type="button" className={styles.secondary} onClick={onReload}>
          Tải lại
        </button>
      </div>
    </form>
  );
}
