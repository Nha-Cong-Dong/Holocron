"use client";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { Icon } from "@/components/common/Icon";
import { Button, Dropdown, type DropdownOption, Field } from "@/components/ui";
import { importChapterOptions } from "@/lib/chapters";
import { SOURCE_KIND_LABEL_VI, SOURCE_KINDS, sourceKindLabel } from "@/lib/documents";
import { importFailureText } from "@/lib/errors";
import {
  CHAPTER_MISSING_VI,
  chapterLabel,
  defaultTitle,
  isStaged,
  type RowEdit,
  TITLE_TOO_LONG_VI,
} from "@/lib/import-queue";
import type { SourceKind } from "@/types/holocron";
import type { FileImportRow } from "./ImportQueueProvider";
import styles from "./ImportRow.module.css";

/** Giá trị của "Chưa rõ chương" trong bảng chọn chương. */
const NO_CHAPTER = "";

const SOURCE_KIND_OPTIONS: readonly DropdownOption<SourceKind>[] = SOURCE_KINDS.map((kind) => ({
  value: kind,
  label: SOURCE_KIND_LABEL_VI[kind],
}));

function chapterOptions(current: string | null): DropdownOption<string>[] {
  const codes = importChapterOptions([], current);
  const options = codes.map((code) => ({ value: code, label: chapterLabel(code) }));
  return current === null ? [{ value: NO_CHAPTER, label: chapterLabel(null) }, ...options] : options;
}

interface ImportRowProps {
  row: FileImportRow;
  /** Dòng đang bị bỏ: chạy `m-row-out` rồi mới xoá khỏi hàng đợi (`onLeft`). */
  leaving?: boolean;
  /** Sửa dòng; chỉ dòng chưa bấm đọc mới sửa được. */
  onEdit?: (edit: RowEdit) => void;
  onRemove: () => void;
  onRetry: () => void;
  onLeft?: () => void;
}

/**
 * Một file trong hàng đợi nhập (màn 4): khi đang sửa thì sửa tên tại chỗ, chọn chương (đoán từ tên file; không đoán
 * được thì "Chưa rõ chương" tô vàng) và loại tài liệu; sau khi bấm đọc thì hiện kết quả gửi của riêng file đó.
 */
export function ImportRow({ row, leaving = false, onEdit, onRemove, onRetry, onLeft }: ImportRowProps) {
  const staged = isStaged(row) && onEdit !== undefined;
  const name = row.file.name;
  const chapterUnknown = row.chapterCode === null;
  const titleProblem = row.problem === TITLE_TOO_LONG_VI ? row.problem : undefined;
  const fileProblem =
    row.problem !== null && row.problem !== CHAPTER_MISSING_VI && row.problem !== TITLE_TOO_LONG_VI
      ? row.problem
      : null;

  return (
    <li
      data-flip-key={row.id}
      className={`${leaving ? "m-row-out" : "m-file-in"} ${styles.row}`}
      data-status={row.status}
      onAnimationEnd={(event) => {
        if (leaving && event.target === event.currentTarget) onLeft?.();
      }}
    >
      <span className={styles.icon}>
        <Icon name="pdf" size={22} />
      </span>
      <div className={styles.body}>
        <p className={styles.fileName} title={name}>
          {name}
        </p>
        {staged ? (
          <>
            <Field
              label="Tên tài liệu"
              value={row.title}
              onChange={(event) => onEdit?.({ title: event.target.value })}
              hint={row.title.trim() === "" ? `Bỏ trống thì dùng tên file “${defaultTitle(name)}”.` : undefined}
              error={titleProblem}
              disabled={leaving}
              spellCheck={false}
              autoComplete="off"
            />
            <div className={styles.selects}>
              <div className={styles.chapter} data-unknown={chapterUnknown ? "true" : undefined}>
                <Dropdown
                  label="Chương"
                  options={chapterOptions(row.chapterCode)}
                  value={row.chapterCode ?? NO_CHAPTER}
                  onChange={(value) => onEdit?.({ chapterCode: value === NO_CHAPTER ? null : value })}
                  disabled={leaving}
                />
                {chapterUnknown && <p className={styles.warnText}>{CHAPTER_MISSING_VI}</p>}
              </div>
              <Dropdown
                label="Loại tài liệu"
                options={SOURCE_KIND_OPTIONS}
                value={row.sourceKind}
                onChange={(value) => onEdit?.({ sourceKind: value })}
                disabled={leaving}
              />
            </div>
            {fileProblem !== null && (
              <p className={styles.errText}>
                Chưa thêm được “{name}”. {fileProblem}
              </p>
            )}
          </>
        ) : (
          <>
            <p className={styles.title}>{row.title.trim() === "" ? defaultTitle(name) : row.title}</p>
            <p className={styles.meta}>
              {chapterLabel(row.chapterCode)} · {sourceKindLabel(row.sourceKind)}
            </p>
            <RowOutcome row={row} />
          </>
        )}
      </div>
      <div className={styles.actions}>
        {row.status === "failed" && (
          <Button variant="secondary" onClick={onRetry} disabled={leaving}>
            Thử lại
          </Button>
        )}
        {row.status !== "sending" && !(row.status === "ready" && row.queued) && (
          <Button variant="icon" aria-label={`Bỏ ${name} khỏi danh sách`} onClick={onRemove} disabled={leaving}>
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </Button>
        )}
      </div>
    </li>
  );
}

/** Kết quả gửi của một file đã bấm đọc. */
function RowOutcome({ row }: { row: FileImportRow }) {
  switch (row.status) {
    case "ready":
      return <p className={styles.status}>Đang chờ gửi</p>;
    case "sending":
      return (
        <p className={styles.status}>
          <HolocronMark size={18} spinning />
          Đang gửi…
        </p>
      );
    case "created":
      return (
        <p className={styles.status} data-tone="ok">
          <span className={`m-done-pop ${styles.done}`}>
            <Icon name="correct" size={18} />
          </span>
          Đã nhận, đang đọc
        </p>
      );
    case "existing":
      return (
        <p className={styles.status} data-tone="ok">
          <Icon name="correct" size={18} />
          Đã có tài liệu này trong môn, không đọc lại
        </p>
      );
    case "conflict":
    case "failed": {
      if (row.error === null) return null;
      const text = importFailureText(row.file.name, row.error);
      return (
        <div className={styles.failure} role="alert">
          <p className={styles.failureTitle}>{text.title}</p>
          <p>{text.fix}</p>
        </div>
      );
    }
    case "invalid":
      return row.problem === null ? null : <p className={styles.errText}>{row.problem}</p>;
  }
}
