"use client";

import { type DragEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/common/Icon";
import { Button } from "@/components/ui";
import { useFlip } from "@/hooks/motion-hooks";
import { type LeavingRow, START_BLOCK_MESSAGE_VI, startDecision, withLeavingRows } from "@/lib/import-queue";
import { DOCUMENT_MAX_PAGES, DOCUMENT_UPLOAD_MAX_MIB } from "@/lib/limits";

import styles from "./BatchImport.module.css";
import { type FileImportRow, useImportQueue } from "./ImportQueueProvider";
import { ImportRow } from "./ImportRow";

/** Thời gian chờ tối đa của `m-row-out` (160 ms) trước khi xoá dòng, phòng khi trình duyệt không chạy animation. */
const ROW_OUT_FALLBACK_MS = 260;

interface BatchImportProps {
  subjectCode: string;
  /** Các dòng đang sửa (chưa bấm đọc) của môn. */
  rows: readonly FileImportRow[];
  /** Quay lại danh sách tài liệu mà không gửi (các dòng đang sửa vẫn giữ). */
  onBack: () => void;
  /** Đã bấm "Bắt đầu đọc": màn chuyển sang danh sách, tiến độ gửi và đọc. */
  onStarted: () => void;
}

/**
 * Màn 4 "Thêm hàng loạt" (design-handoff/README.md): kéo thả nhiều PDF (vùng thả phát sáng) hoặc "Chọn file trên máy";
 * mỗi file một dòng sửa được; nút chính "Bắt đầu đọc (N file)" xếp các dòng hợp lệ vào hàng đợi gửi lần lượt.
 */
export function BatchImport({ subjectCode, rows, onBack, onStarted }: BatchImportProps) {
  const ids = useId();
  const queue = useImportQueue();
  const fileInput = useRef<HTMLInputElement>(null);
  const listRef = useFlip<HTMLUListElement>();
  const [dragging, setDragging] = useState(false);
  // Dòng vừa bỏ: đã xoá khỏi hàng đợi ngay khi bấm (không thể bị gửi), chỉ còn bản vẽ chạy `m-row-out`.
  const [leaving, setLeaving] = useState<readonly LeavingRow<FileImportRow>[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const dropLeaving = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) clearTimeout(timer);
    timers.current.delete(id);
    setLeaving((previous) => previous.filter((item) => item.row.id !== id));
  }, []);

  function remove(row: FileImportRow): void {
    const index = rows.findIndex((item) => item.id === row.id);
    queue.remove(row.id);
    setLeaving((previous) => [...previous, { row, index: index === -1 ? rows.length : index }]);
    timers.current.set(
      row.id,
      setTimeout(() => dropLeaving(row.id), ROW_OUT_FALLBACK_MS),
    );
  }

  function addFiles(list: FileList | null): void {
    if (list === null || list.length === 0) return;
    queue.add(subjectCode, Array.from(list));
  }

  function onDrop(event: DragEvent<HTMLElement>): void {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  const decision = startDecision(rows);
  const hasRows = rows.length > 0;
  const shown = withLeavingRows(rows, leaving);
  const leavingIds = new Set(leaving.map((item) => item.row.id));

  function start(): void {
    if (decision.block !== null) return;
    queue.start(subjectCode);
    onStarted();
  }

  return (
    <section className={styles.root} aria-labelledby={`${ids}-title`}>
      <div className={styles.head}>
        <h2 id={`${ids}-title`} className={styles.title}>
          {hasRows ? "Kiểm lại trước khi đọc" : "Thêm tài liệu"}
        </h2>
        <p className={styles.sub}>
          {hasRows
            ? "Sửa tên hoặc chương nếu đoán chưa đúng. Mỗi file là một chương."
            : "Thả nhiều file cùng lúc được. Chương được đoán từ tên file."}
        </p>
      </div>

      {shown.length > 0 && (
        <ul ref={listRef} className={styles.rows} aria-label="File sẽ đọc">
          {shown.map((row) =>
            leavingIds.has(row.id) ? (
              <ImportRow
                key={row.id}
                row={row}
                leaving
                onRemove={() => dropLeaving(row.id)}
                onRetry={() => dropLeaving(row.id)}
                onLeft={() => dropLeaving(row.id)}
              />
            ) : (
              <ImportRow
                key={row.id}
                row={row}
                onEdit={(edit) => queue.edit(row.id, edit)}
                onRemove={() => remove(row)}
                onRetry={() => queue.retry(row.id)}
              />
            ),
          )}
        </ul>
      )}

      {/* Kéo thả chỉ dành cho chuột; nút "Chọn file trên máy" là cách thay thế cho bàn phím và trình đọc màn hình. */}
      <section
        aria-label="Vùng thả file PDF"
        className={`${styles.dropzone} ${dragging ? "is-dragover" : ""}`}
        data-compact={hasRows ? "true" : undefined}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          const next = event.relatedTarget;
          if (!(next instanceof Node && event.currentTarget.contains(next))) setDragging(false);
        }}
        onDrop={onDrop}
      >
        <span className={`m-dropzone-glow ${styles.glow}`} aria-hidden="true" />
        <span className={`m-dropzone-icon ${styles.dropIcon}`}>
          <Icon name="drop" size={hasRows ? 22 : 32} />
        </span>
        <p className={styles.dropTitle}>{hasRows ? "Kéo thêm PDF vào đây, hoặc" : "Kéo file PDF bài giảng vào đây"}</p>
        <p className={styles.hint}>
          Mỗi file tối đa {DOCUMENT_UPLOAD_MAX_MIB} MiB và {DOCUMENT_MAX_PAGES} trang. PDF đặt mật khẩu mở không nhập
          được.
        </p>
        <input
          ref={fileInput}
          type="file"
          accept=".pdf"
          multiple
          className="visually-hidden"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        {hasRows ? (
          <Button variant="secondary" onClick={() => fileInput.current?.click()}>
            Chọn thêm file
          </Button>
        ) : (
          <Button variant="primary" onClick={() => fileInput.current?.click()}>
            Chọn file trên máy
          </Button>
        )}
      </section>

      <div className={styles.footer}>
        <p id={`${ids}-summary`} className={styles.summary} aria-live="polite">
          {!hasRows
            ? "Chưa có file nào."
            : decision.block === null
              ? `${decision.count} file sẵn sàng đọc.`
              : START_BLOCK_MESSAGE_VI[decision.block]}
        </p>
        <div className={styles.footerActions}>
          <Button variant="secondary" onClick={onBack}>
            Quay lại danh sách
          </Button>
          {hasRows && (
            <Button
              variant="primary"
              onClick={start}
              aria-disabled={decision.block !== null}
              aria-describedby={`${ids}-summary`}
            >
              Bắt đầu đọc ({decision.count} file)
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}
