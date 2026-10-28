"use client";

import Link from "next/link";

import { EmptyState } from "@/components/common/LoadState";
import { PAIRS_CHUNK_PREVIEW_COUNT } from "@/lib/limits";
import { chunksToPick, type PairsLocation, pairsHref } from "@/lib/pairs";
import { type ChunkView, pageRangeLabel, vaultHref } from "@/lib/vault";

import styles from "./Pairs.module.css";

/** Số ký tự đầu của văn bản chunk hiện ở danh sách chọn (chỉ để nhận ra chunk, không phải trích dẫn). */
const SNIPPET_CHARS = 160;

function snippet(text: string): string {
  const chars = [...text];
  return chars.length <= SNIPPET_CHARS ? text : `${chars.slice(0, SNIPPET_CHARS).join("")}…`;
}

interface ChunkPickerProps {
  location: PairsLocation;
  subjectCode: string;
  map: ReadonlyMap<string, ChunkView>;
  /** Map chưa đủ (chạm trần số request). */
  partial: boolean;
  /** Dựng địa chỉ mở một chunk (giữ bộ lọc); mặc định `pairsHref` (`/pairs?…`). */
  hrefFor?: (location: Partial<PairsLocation>) => string;
  /** Địa chỉ phần Tài liệu của môn; mặc định `/vault?subject=`. */
  documentsHref?: string;
}

/**
 * Danh sách chunk để chọn khi chưa mở chunk nào: đủ chunk của chương đang lọc, hoặc vài chunk đầu của môn khi chưa
 * chọn chương. Mỗi chunk là một liên kết `?chunk=` (giữ bộ lọc hiện tại).
 */
export function ChunkPicker({
  location,
  subjectCode,
  map,
  partial,
  hrefFor = pairsHref,
  documentsHref,
}: ChunkPickerProps) {
  const { chunks, truncated } = chunksToPick(map, location.chapter, PAIRS_CHUNK_PREVIEW_COUNT);
  return (
    <>
      {partial && <p className={styles.warningNote}>Chưa tải hết chunk của môn; danh sách dưới đây có thể thiếu.</p>}
      {chunks.length === 0 ? (
        <EmptyState
          title={
            location.chapter === null
              ? "Môn này chưa có văn bản để tạo khái niệm."
              : "Chương này chưa có đoạn văn bản nào."
          }
        >
          {location.chapter === null ? (
            <>
              Văn bản có sau khi bạn xác nhận mọi trang của một tài liệu.{" "}
              <Link href={documentsHref ?? vaultHref({ subject: subjectCode })}>Mở phần Tài liệu</Link> để thêm PDF hoặc
              xác nhận trang.
            </>
          ) : (
            "Bỏ lọc chương hoặc chọn chương khác."
          )}
        </EmptyState>
      ) : (
        <>
          <p className={styles.hint}>Chọn một chunk để bôi đoạn định nghĩa.</p>
          <ul className={styles.pickList}>
            {chunks.map((chunk) => (
              <li key={chunk.chunkId} className={styles.pickItem}>
                <p className={styles.meta}>
                  <span className={styles.pageLabel}>{pageRangeLabel(chunk)}</span>
                  <span>
                    Chương <span className={styles.code}>{chunk.chapterCode}</span>
                  </span>
                </p>
                <p className={styles.snippet}>{snippet(chunk.rawText)}</p>
                <div className={styles.actions}>
                  <Link
                    className={styles.secondary}
                    href={hrefFor({ ...location, subject: subjectCode, chunk: chunk.chunkId })}
                  >
                    Mở chunk này
                  </Link>
                </div>
              </li>
            ))}
          </ul>
          {truncated && <p className={styles.hint}>Đang hiện các chunk đầu tiên của môn. Chọn chương để xem đủ.</p>}
        </>
      )}
    </>
  );
}
