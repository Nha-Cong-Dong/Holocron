"use client";

import Link from "next/link";
import type { RefObject } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import type { UiError } from "@/lib/errors";
import { type ChunkMapStatus, type ConceptPairList, lookupChunk } from "@/lib/pairs";
import { type ChunkView, vaultHref } from "@/lib/vault";
import type { ConceptPair } from "@/types/holocron";

import { PairCard } from "./PairCard";
import styles from "./Pairs.module.css";

export type PairListState =
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; value: ConceptPairList };

interface PairListProps {
  subjectCode: string;
  state: PairListState;
  /** Đang lọc theo chương hoặc trạng thái (để câu trạng thái trống nói đúng lý do). */
  filtered: boolean;
  mapStatus: ChunkMapStatus | "loading";
  map: ReadonlyMap<string, ChunkView>;
  editingPairId: string | null;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onEdit: (pair: ConceptPair) => void;
  onChanged: (pairId: string) => void;
  onReloadList: () => void;
  onReloadAll: () => void;
}

/** Danh sách cặp theo đúng thứ tự backend trả `(chapter_code, term, pair_id)`; không hiện số đếm theo trạng thái. */
export function PairList({
  subjectCode,
  state,
  filtered,
  mapStatus,
  map,
  editingPairId,
  headingRef,
  onEdit,
  onChanged,
  onReloadList,
  onReloadAll,
}: PairListProps) {
  return (
    <section className={styles.panel} aria-labelledby="pairs-list-title">
      <h2 id="pairs-list-title" ref={headingRef} tabIndex={-1}>
        Cặp khái niệm
      </h2>
      {state.kind === "loading" && <Loading label="Đang tải cặp khái niệm…" />}
      {state.kind === "error" && <ErrorNotice error={state.error} onRetry={onReloadList} onReload={onReloadList} />}
      {state.kind === "ready" && state.value.pairs.length === 0 && (
        <EmptyState title={filtered ? "Không có cặp nào khớp bộ lọc." : "Môn này chưa có cặp khái niệm nào."}>
          {filtered ? (
            "Đổi chương hoặc trạng thái để xem các cặp khác, hoặc bôi định nghĩa trong một chunk ở cột bên."
          ) : (
            <>
              Chọn một chunk ở cột bên rồi bôi đoạn định nghĩa để tạo cặp. Chưa có văn bản thì{" "}
              <Link href={vaultHref({ subject: subjectCode })}>mở Vault</Link> để nhập và xác nhận tài liệu.
            </>
          )}
        </EmptyState>
      )}
      {state.kind === "ready" && state.value.hasSkipped && (
        <p className={styles.warningNote}>Có cặp backend trả về sai dạng nên không hiển thị.</p>
      )}
      {state.kind === "ready" && state.value.pairs.length > 0 && (
        <ul className={styles.list}>
          {state.value.pairs.map((pair) => (
            <PairCard
              key={pair.pair_id}
              pair={pair}
              lookup={lookupChunk(mapStatus, map, pair.chunk_id)}
              isEditing={editingPairId === pair.pair_id}
              onEdit={onEdit}
              onChanged={onChanged}
              onReloadList={onReloadList}
              onReloadAll={onReloadAll}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
