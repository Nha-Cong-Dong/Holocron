"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { useShell } from "@/components/shell/ShellContext";
import { Button, EmptyState, ErrorState, Skeleton, useToast } from "@/components/ui";
import { useReducedMotion } from "@/hooks/motion-hooks";
import { api } from "@/lib/api";
import {
  acceptAllGate,
  type BulkCandidate,
  bulkMessageOf,
  bulkReducer,
  type ChangeNote,
  type ConceptFocus,
  type ConceptView,
  changeEffects,
  conceptErrorText,
  conceptsScreen,
  conceptView,
  failedReloadEffects,
  focusAfterRemoval,
  groupPairs,
  IDLE_BULK,
  mergeChange,
  newlyConfirmed,
  nextReloadNote,
  type ReloadNote,
  reloadedFocus,
  reloadNoteText,
  screenErrorText,
  sendQueued,
  staleView,
  visibleConceptEntrances,
  withoutIds,
  withStale,
} from "@/lib/concept-board";
import { toUiError, type UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { PAIRS_CHUNK_MAX_REQUESTS, PAIRS_CHUNK_PAGE_SIZE } from "@/lib/limits";
import { type ChunkMapResult, loadChunkMap, type PairsLocation, parsePairsLocation, runLatest } from "@/lib/pairs";
import { freshRuntime, isCapabilityBlocked } from "@/lib/runtime";
import { buildSkyLayout } from "@/lib/sky";
import { skyGains } from "@/lib/sky-gains";
import type { ChunkView } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import { type AcceptedEntry, AcceptedList } from "./AcceptedList";
import styles from "./ConceptsTab.module.css";
import { PassagePanel } from "./PassagePanel";
import type { CardLeaving } from "./ProposalCard";
import { type ProposalEntry, ProposalList } from "./ProposalList";

/**
 * Đề xuất khái niệm tự động chưa có trong bản này: lối vào bị ẩn hẳn. Khi contract khả năng tương ứng được duyệt và
 * merge, thay hằng này bằng đúng một lời gọi `capabilityOf(runtime, …)`.
 */
const AUTO_PROPOSAL_AVAILABLE = false;

/** Hết giờ dự phòng chờ animation rời đi của thẻ (animation thật dài 200–220 ms). */
const LEAVE_FALLBACK_MS = 320;

const EMPTY_MAP: ReadonlyMap<string, ChunkView> = new Map();
const SKELETON_CARDS = ["a", "b"] as const;

type MapState = { status: "loading" } | ChunkMapResult;
type ListState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; pairs: ConceptPair[]; hasSkipped: boolean };

type Operation = "accept" | "reject" | "rename" | "restore";

/** Thẻ đã xử lý xong của một thao tác, gửi kèm lượt tải lại danh sách. */
interface Settled {
  /** Thẻ đề xuất đã cho rời đi bằng animation (chỉ khi không giảm chuyển động): tải lại lỗi thì hiện lại. */
  left?: readonly string[];
  /** Thẻ đề xuất đã ghi thành công: tải lại lỗi thì khoá kèm lý do tới khi danh sách đọc lại được. */
  done?: readonly string[];
  /** Lượt tải này do bấm "Thử lại" ở dòng báo tải lại lỗi. */
  retry?: boolean;
}

interface CardError {
  operation: Operation;
  term: string | null;
  text: string;
}

export interface ConceptsTabProps {
  subjectCode: string;
  /** Địa chỉ tab Khái niệm của môn kèm `?chunk=` (đoạn đang mở ở khung tự tạo). */
  hrefFor: (location: Partial<PairsLocation>) => string;
  /** Sang tab Tài liệu (trạng thái rỗng). */
  onOpenDocuments: () => void;
  /** Sang tab Ôn tập (thẻ mời sau khi duyệt hết đề xuất). */
  onOpenPractice: () => void;
  /** Danh sách khái niệm vừa đổi do thao tác ở đây: trang môn tải lại bầu trời. */
  onConceptsChanged: () => void;
}

/**
 * Tab Khái niệm của trang môn (design-handoff/README.md màn 7): khối "Đề xuất" (thẻ có trích dẫn nguyên văn, Bỏ / Sửa /
 * Nhận, "Nhận tất cả"), thẻ mời sang Ôn tập khi đã duyệt hết, khung "Tự tạo khái niệm" (bôi đoạn hoặc chọn câu) và cột
 * "Đã nhận". Sau mỗi thao tác, danh sách được đọc lại từ backend (không vá tại chỗ).
 */
export function ConceptsTab({
  subjectCode,
  hrefFor,
  onOpenDocuments,
  onOpenPractice,
  onConceptsChanged,
}: ConceptsTabProps) {
  const router = useRouter();
  const location = parsePairsLocation(useSearchParams());
  const { runtime } = useShell();
  const toast = useToast();
  const reduced = useReducedMotion();
  // Đọc qua ref để lượt tải không phụ thuộc vào việc cha có giữ nguyên hàm hay không (tránh tải lại vô hạn).
  const toastRef = useRef(toast);
  const changedRef = useRef(onConceptsChanged);
  useEffect(() => {
    toastRef.current = toast;
    changedRef.current = onConceptsChanged;
  }, [toast, onConceptsChanged]);

  // -------------------------------------------------------------------- đoạn văn của môn
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
              subjectCode,
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
  }, [subjectCode]);

  // -------------------------------------------------------------------- tên tài liệu
  const [docTitles, setDocTitles] = useState<ReadonlyMap<string, string>>(new Map());
  const docCalls = useRef(createLatestCounter());
  const loadDocs = useCallback(() => {
    void runLatest(
      docCalls.current,
      () => api.listDocuments(subjectCode),
      (outcome) => {
        // Không có tên tài liệu thì trích dẫn chỉ hiện mã tài liệu; không chặn màn.
        if (outcome.ok) setDocTitles(new Map(outcome.value.map((doc) => [doc.docId, doc.title])));
      },
    );
  }, [subjectCode]);

  // -------------------------------------------------------------------- danh sách khái niệm
  const [listState, setListState] = useState<ListState>({ kind: "loading" });
  const [reloadNote, setReloadNote] = useState<ReloadNote | null>(null);
  const [reloading, setReloading] = useState(false);
  // Thẻ đề xuất đã ghi thành công mà danh sách chưa đọc lại được: khoá Nhận/Bỏ/Sửa tới lượt tải thành công.
  const [stale, setStale] = useState<ReadonlySet<string>>(new Set());
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  const [leaving, setLeaving] = useState<ReadonlyMap<string, CardLeaving>>(new Map());
  const previousPairs = useRef<ConceptPair[] | null>(null);
  const focusRequest = useRef<ConceptFocus | null>(null);
  // Việc của các thao tác đã thành công mà chưa phát (toast, focus, tải lại bầu trời). Chỉ lượt tải lại mới nhất được
  // áp, nên việc của thao tác trước dồn vào đây và phát ở lượt mới nhất, không mất khi hai thao tác gần nhau.
  const pendingChange = useRef<ChangeNote | null>(null);
  // Thẻ đề xuất đã cho rời đi mà danh sách mới chưa về: lượt tải lại lỗi thì các thẻ này hiện lại.
  const pendingLeft = useRef<string[]>([]);
  const pendingDone = useRef<string[]>([]);
  const pendingRetry = useRef(false);
  const listCalls = useRef(createLatestCounter());

  // -------------------------------------------------------------------- focus
  const proposalsHeading = useRef<HTMLHeadingElement>(null);
  const acceptedHeading = useRef<HTMLHeadingElement>(null);
  const inviteRef = useRef<HTMLDivElement>(null);
  const focusTarget = useCallback((target: ConceptFocus) => {
    const fallback = proposalsHeading.current ?? inviteRef.current ?? acceptedHeading.current;
    if (target.kind === "concept") {
      const element = document.querySelector<HTMLElement>(`[data-concept-id="${CSS.escape(target.id)}"]`);
      (element ?? fallback)?.focus();
      return;
    }
    if (target.kind === "accepted") {
      acceptedHeading.current?.focus();
      return;
    }
    fallback?.focus();
  }, []);

  const loadPairs = useCallback(
    (keepCurrent: boolean, change: ChangeNote | null, settled: Settled = {}) => {
      if (!keepCurrent) setListState({ kind: "loading" });
      pendingChange.current = mergeChange(pendingChange.current, change);
      pendingLeft.current = [...pendingLeft.current, ...(settled.left ?? [])];
      pendingDone.current = [...pendingDone.current, ...(settled.done ?? [])];
      pendingRetry.current = pendingRetry.current || settled.retry === true;
      void runLatest(
        listCalls.current,
        () => api.listConceptPairs({ subjectCode, chapterCode: null, status: null }),
        (outcome) => {
          const pending = pendingChange.current;
          const left = pendingLeft.current;
          const done = pendingDone.current;
          const retried = pendingRetry.current;
          pendingChange.current = null;
          pendingLeft.current = [];
          pendingDone.current = [];
          pendingRetry.current = false;
          setReloading(false);
          if (!outcome.ok) {
            // Thao tác đã thành công dù chưa đọc lại được danh sách: vẫn báo, chỉ không nói số sao.
            const effects = failedReloadEffects(pending, left);
            for (const text of effects.toasts) toastRef.current.show(text);
            if (effects.reloadSky) changedRef.current();
            if (keepCurrent && previousPairs.current !== null) {
              // Danh sách cũ còn hiện: thẻ đã rời đi hiện lại, focus rời thẻ đang ẩn về tiêu đề khối.
              // Thẻ đã ghi hiện lại với dữ liệu cũ: khoá kèm lý do tới khi đọc lại được danh sách.
              setLeaving((previous) => withoutIds(previous, effects.reveal));
              setStale((previous) => withStale(previous, done));
              // Câu mới và vùng alert dựng lại ở mỗi lần lỗi để trình đọc màn hình đọc lại.
              const text = reloadNoteText(outcome.error, retried ? new Date() : null);
              setReloadNote((previous) => nextReloadNote(previous, text));
              if (effects.focus !== null) focusTarget(effects.focus);
            } else setListState({ kind: "error", error: outcome.error });
            return;
          }
          const next = outcome.value.pairs;
          const previous = previousPairs.current;
          const gains = skyGains(previous === null ? null : buildSkyLayout("", previous), buildSkyLayout("", next));
          const effects = changeEffects(pending, gains.total);
          for (const text of effects.toasts) toastRef.current.show(text);
          // Bấm "Thử lại" ở dòng báo: nút biến mất cùng dòng báo nên focus về tiêu đề khối.
          const focus = reloadedFocus(effects.focus, retried);
          if (focus !== null) focusRequest.current = focus;
          if (effects.reloadSky) changedRef.current();
          previousPairs.current = next;
          setFresh(newlyConfirmed(previous, next));
          setLeaving(new Map());
          setStale(new Set());
          setReloadNote(null);
          setListState({ kind: "ready", pairs: next, hasSkipped: outcome.value.hasSkipped });
        },
      );
    },
    [subjectCode, focusTarget],
  );

  const reloadAll = useCallback(() => {
    loadMap();
    loadDocs();
    loadPairs(false, null);
  }, [loadMap, loadDocs, loadPairs]);

  useEffect(() => {
    reloadAll();
    const counters = [mapCalls.current, docCalls.current, listCalls.current];
    return () => {
      for (const counter of counters) counter.invalidate();
    };
  }, [reloadAll]);

  // -------------------------------------------------------------------- focus sau khi tải lại
  useEffect(() => {
    if (listState.kind !== "ready" || focusRequest.current === null) return;
    const target = focusRequest.current;
    focusRequest.current = null;
    focusTarget(target);
  }, [listState, focusTarget]);

  // -------------------------------------------------------------------- thẻ rời đi
  const leaveCallbacks = useRef(new Map<string, () => void>());
  const finishLeave = useCallback((pairId: string) => {
    const callback = leaveCallbacks.current.get(pairId);
    if (callback === undefined) return;
    leaveCallbacks.current.delete(pairId);
    callback();
  }, []);
  const leave = useCallback(
    (pairId: string, kind: Exclude<CardLeaving, null>, then: () => void) => {
      // Giảm chuyển động: không chờ animation.
      if (reduced) {
        then();
        return;
      }
      leaveCallbacks.current.set(pairId, then);
      setLeaving((previous) => new Map(previous).set(pairId, kind));
      window.setTimeout(() => finishLeave(pairId), LEAVE_FALLBACK_MS);
    },
    [reduced, finishLeave],
  );

  // -------------------------------------------------------------------- thao tác với một khái niệm
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [cardErrors, setCardErrors] = useState<ReadonlyMap<string, CardError>>(new Map());
  const [bulk, dispatchBulk] = useReducer(bulkReducer, IDLE_BULK);
  const bulkActive = useRef(false);

  function setBusy(pairId: string, on: boolean): void {
    setBusyIds((previous) => {
      const next = new Set(previous);
      if (on) next.add(pairId);
      else next.delete(pairId);
      return next;
    });
  }

  function setCardError(pairId: string, error: CardError | null): void {
    setCardErrors((previous) => {
      const next = new Map(previous);
      if (error === null) next.delete(pairId);
      else next.set(pairId, error);
      return next;
    });
  }

  const pairs = listState.kind === "ready" ? listState.pairs : [];
  const groups = useMemo(() => groupPairs(pairs), [pairs]);
  const mapStatus = mapState.status;
  const map = mapState.status === "loading" ? EMPTY_MAP : mapState.map;

  /** Cách hiện một thẻ đề xuất: thẻ đã ghi mà chờ cập nhật danh sách thì khoá mọi nút kèm lý do. */
  function proposalView(pair: ConceptPair): ConceptView {
    const view = conceptView(pair, mapStatus, map);
    return stale.has(pair.pair_id) ? staleView(view) : view;
  }

  function retryReload(): void {
    if (reloading) return;
    setReloading(true);
    loadPairs(true, null, { retry: true });
  }

  async function run(pair: ConceptPair, operation: Operation, term: string | null): Promise<void> {
    if (busyIds.has(pair.pair_id) || bulkActive.current || stale.has(pair.pair_id)) return;
    setBusy(pair.pair_id, true);
    setCardError(pair.pair_id, null);
    dispatchBulk({ type: "dismiss", pairId: pair.pair_id });
    try {
      if (operation === "reject") await api.rejectConceptPair(pair.pair_id, pair.version);
      else await api.confirmConceptPair(pair.pair_id, pair.version, term === null ? {} : { term });
    } catch (caught) {
      const error = toUiError(caught);
      setBusy(pair.pair_id, false);
      setCardError(pair.pair_id, { operation, term, text: conceptErrorText(error) });
      // Bản trên máy đã cũ (409) hay khái niệm không còn (404): đọc lại để thẻ mang phiên bản mới.
      if (error.kind === "version_conflict" || error.kind === "state_conflict" || error.kind === "not_found") {
        loadPairs(true, null);
      }
      return;
    }
    setBusy(pair.pair_id, false);
    const accepted = operation === "accept" || operation === "restore" ? 1 : 0;
    if (pair.status === "proposed") {
      const nextId = focusAfterRemoval(
        groups.proposed.map((item) => item.pair_id),
        pair.pair_id,
      );
      const focus: ConceptFocus = nextId === null ? { kind: "proposals" } : { kind: "concept", id: nextId };
      leave(pair.pair_id, operation === "reject" ? "reject" : "accept", () =>
        loadPairs(
          true,
          { accepted, messages: [], focus },
          // Giảm chuyển động: thẻ không rời đi nên không có gì phải hiện lại (và không dời focus khi tải lỗi).
          { left: reduced ? [] : [pair.pair_id], done: [pair.pair_id] },
        ),
      );
      return;
    }
    const messages = operation === "rename" && term !== null ? [`Đã đổi tên thành “${term}”`] : [];
    const focus: ConceptFocus = operation === "reject" ? { kind: "accepted" } : { kind: "concept", id: pair.pair_id };
    loadPairs(true, { accepted, messages, focus });
  }

  function retry(pair: ConceptPair): void {
    const error = cardErrors.get(pair.pair_id);
    void run(pair, error?.operation ?? "accept", error?.term ?? null);
  }

  function acceptAll(): void {
    if (bulkActive.current || bulk.running || !allGate.enabled) return;
    const candidates: BulkCandidate[] = groups.proposed
      .filter((pair) => !busyIds.has(pair.pair_id))
      .map((pair) => {
        const gate = proposalView(pair).actions.confirm;
        const allowed = gate.shown && gate.enabled;
        return {
          pairId: pair.pair_id,
          version: pair.version,
          allowed,
          reason: gate.shown && !gate.enabled ? gate.reason : null,
        };
      });
    const started = bulkReducer(bulk, { type: "start", candidates });
    dispatchBulk({ type: "start", candidates });
    if (!started.running) return;
    bulkActive.current = true;
    setCardErrors(new Map());
    const acceptedIds: string[] = [];
    void sendQueued(
      started,
      async (pairId, version) => {
        await api.confirmConceptPair(pairId, version, {});
        acceptedIds.push(pairId);
        if (!reduced) setLeaving((previous) => new Map(previous).set(pairId, "accept"));
      },
      dispatchBulk,
    ).then(() => {
      bulkActive.current = false;
      loadPairs(
        true,
        { accepted: acceptedIds.length, messages: [], focus: { kind: "proposals" } },
        { left: reduced ? [] : acceptedIds, done: acceptedIds },
      );
    });
  }

  const onCreated = useCallback(() => loadPairs(true, { accepted: 0, messages: [], focus: null }), [loadPairs]);
  const onOpenChunk = useCallback(
    (chunkId: string) => router.replace(hrefFor({ chunk: chunkId }), { scroll: false }),
    [router, hrefFor],
  );

  // -------------------------------------------------------------------- hiển thị
  const screen = conceptsScreen(listState.kind, pairs.length, mapStatus, map.size);
  if (screen === "loading") return <ConceptsLoading />;
  if (listState.kind === "error") {
    return (
      <ErrorState
        title="Không tải được khái niệm"
        fix={screenErrorText(listState.error)}
        onRetry={reloadAll}
        technical={listState.error.diagnostic}
      />
    );
  }
  if (screen === "empty") {
    return (
      <EmptyState
        title="Chưa có khái niệm nào"
        description="Khái niệm được rút ra sau khi đọc xong tài liệu."
        action={{ label: "Thêm tài liệu", onClick: onOpenDocuments }}
      />
    );
  }

  function cardBusy(pairId: string): boolean {
    if (busyIds.has(pairId)) return true;
    const item = bulk.items.find((entry) => entry.pairId === pairId);
    return bulk.running && (item?.status === "queued" || item?.status === "sending");
  }

  function cardError(pairId: string): string | null {
    const own = cardErrors.get(pairId);
    if (own !== undefined) return own.text;
    const item = bulk.items.find((entry) => entry.pairId === pairId);
    return item?.status === "conflict" || item?.status === "failed" ? bulkMessageOf(bulk, pairId) : null;
  }

  const titleOf = (view: ConceptView): string | null =>
    view.chunk === null ? null : (docTitles.get(view.chunk.docId) ?? null);
  const proposalEntries: ProposalEntry[] = groups.proposed.map((pair) => {
    const view = proposalView(pair);
    return {
      pair,
      view,
      docTitle: titleOf(view),
      busy: cardBusy(pair.pair_id),
      leaving: leaving.get(pair.pair_id) ?? null,
      error: cardError(pair.pair_id),
    };
  });
  const toAccepted = (pair: ConceptPair): AcceptedEntry => {
    const view = conceptView(pair, mapStatus, map);
    return {
      pair,
      view,
      docTitle: titleOf(view),
      busy: busyIds.has(pair.pair_id),
      fresh: fresh.has(pair.pair_id),
      error: cardErrors.get(pair.pair_id)?.text ?? null,
    };
  };
  const allGate = acceptAllGate(
    proposalEntries.map((entry) => ({ view: entry.view, busy: entry.busy, stale: stale.has(entry.pair.pair_id) })),
  );
  const entrances = visibleConceptEntrances({ autoProposalAvailable: AUTO_PROPOSAL_AVAILABLE });
  const searchAvailable = !isCapabilityBlocked(freshRuntime(runtime), "vault_search");

  return (
    <div className={styles.root}>
      {mapState.status === "partial" && (
        <p className={styles.note}>
          Môn này có nhiều đoạn văn hơn số Holocron tải được một lần. Khái niệm có đoạn chưa tải sẽ chưa kiểm chứng được
          trang và chưa sửa, nhận được.
        </p>
      )}
      {listState.kind === "ready" && listState.hasSkipped && (
        <p className={styles.note}>Có khái niệm Holocron không đọc được nên không hiện ở đây.</p>
      )}
      {reloadNote !== null && (
        <div className={`${styles.note} ${styles.actions}`}>
          <p key={reloadNote.key} className={styles.inviteText} role="alert">
            {reloadNote.text}
          </p>
          <Button variant="secondary" busy={reloading} busyLabel="Đang tải lại" onClick={retryReload}>
            Thử lại
          </Button>
        </div>
      )}
      <div className={styles.grid}>
        <div className={styles.main}>
          {proposalEntries.length > 0 && (
            <ProposalList
              entries={proposalEntries}
              headingRef={proposalsHeading}
              bulkRunning={bulk.running}
              acceptAllReason={!bulk.running && !allGate.enabled ? allGate.reason : null}
              onAcceptAll={acceptAll}
              onAccept={(pair, term) => void run(pair, "accept", term)}
              onReject={(pair) => void run(pair, "reject", null)}
              onRetry={retry}
              onLeft={(pair) => finishLeave(pair.pair_id)}
            />
          )}
          {proposalEntries.length === 0 && groups.confirmed.length > 0 && (
            <div ref={inviteRef} className={styles.invite} tabIndex={-1}>
              <p className={styles.inviteText}>
                <b>Đã duyệt hết đề xuất.</b> Có {groups.confirmed.length} khái niệm để ôn.
              </p>
              <Button variant="primary" onClick={onOpenPractice}>
                Ôn tập ngay
              </Button>
            </div>
          )}
          {entrances.includes("manual_create") && (
            <PassagePanel
              subjectCode={subjectCode}
              mapStatus={mapStatus}
              map={map}
              openChunkId={location.chunk}
              onOpenChunk={onOpenChunk}
              onRetryMap={loadMap}
              searchAvailable={searchAvailable}
              onCreated={onCreated}
            />
          )}
        </div>
        <AcceptedList
          confirmed={groups.confirmed.map(toAccepted)}
          rejected={groups.rejected.map(toAccepted)}
          headingRef={acceptedHeading}
          onRename={(pair, term) => void run(pair, "rename", term)}
          onReject={(pair) => void run(pair, "reject", null)}
          onRestore={(pair) => void run(pair, "restore", null)}
        />
      </div>
    </div>
  );
}

function ConceptsLoading() {
  return (
    <div className={styles.loading} aria-busy="true">
      <div className={styles.skeletonGrid} aria-hidden="true">
        <div className={styles.skeletonCol}>
          <Skeleton width="30%" height={22} />
          {SKELETON_CARDS.map((key) => (
            <Skeleton key={key} height={150} />
          ))}
        </div>
        <div className={styles.skeletonCol}>
          <Skeleton width="50%" height={22} />
          <Skeleton height={44} />
          <Skeleton height={44} />
        </div>
      </div>
      <output className={styles.loadingText}>
        <HolocronMark size={22} spinning />
        Đang tải khái niệm…
      </output>
    </div>
  );
}
