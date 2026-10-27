"use client";

import { type KeyboardEvent, useCallback, useMemo, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { ApiError, api, toUiError } from "@/lib/api";
import { localError, type UiError } from "@/lib/errors";
import { canApprove, cellKey, cellText, groupIssues, keyedIssues, rowIndices, tableColumns } from "@/lib/gate";
import type { GateIssue, IngestionItem, Severity } from "@/types/holocron";

import styles from "./TableAuditor.module.css";

const MAX_CELL_TEXT = 64;

const SEVERITY_LABEL: Record<Severity, string> = {
  blocking: "Chặn",
  warning: "Cảnh báo",
  info: "Thông tin",
};

const STATUS_LABEL: Record<string, string> = {
  AUTO_PASS: "Đạt — sẵn sàng duyệt",
  NEEDS_REVIEW: "Cần xem lại (chỉ còn cảnh báo)",
  BLOCKED: "Bị chặn — còn lỗi BLOCKING",
};

interface EditingCell {
  row: number;
  col: number;
  draft: string;
}

interface TableAuditorProps {
  item: IngestionItem;
  onItemChange: (item: IngestionItem) => void;
  onApproved: (item: IngestionItem) => void;
}

function worstSeverity(issues: GateIssue[]): Severity | null {
  if (issues.some((i) => i.severity === "blocking")) return "blocking";
  if (issues.some((i) => i.severity === "warning")) return "warning";
  if (issues.some((i) => i.severity === "info")) return "info";
  return null;
}

function joinClasses(...classes: (string | undefined)[]): string {
  return classes.filter((c): c is string => c !== undefined && c.length > 0).join(" ");
}

function cellClassName(severity: Severity | null, verified: boolean): string {
  if (severity === "blocking") return joinClasses(styles.cell, styles.cellBlocking);
  if (severity === "warning") return joinClasses(styles.cell, styles.cellWarning);
  if (verified) return joinClasses(styles.cell, styles.cellVerified);
  return joinClasses(styles.cell);
}

/** Lỗi đang hiện cùng thao tác mà nút "Thử lại" sẽ gửi lại; nút "Tải lại bản mới nhất" luôn chỉ đọc lại item. */
interface AuditorError {
  info: UiError;
  retry: (() => void) | null;
}

export function TableAuditor({ item, onItemChange, onApproved }: TableAuditorProps) {
  const [editing, setEditing] = useState<EditingCell | null>(null);
  const [busy, setBusy] = useState<"patch" | "approve" | "reload" | null>(null);
  const [error, setError] = useState<AuditorError | null>(null);

  const table = item.table_primary;
  const rows = rowIndices(table);
  const columns = tableColumns(table);
  const { byCell, tableLevel } = useMemo(() => groupIssues(item.report), [item.report]);
  const blockingCount = item.report?.issues.filter((i) => i.severity === "blocking").length ?? 0;
  const warningCount = item.report?.issues.filter((i) => i.severity === "warning").length ?? 0;
  const approvable = canApprove(item);
  const editable = !item.approved && busy === null;

  const editingIssues = editing === null ? [] : (byCell.get(cellKey(editing.row, editing.col)) ?? []);
  const alternatives = [...new Set(editingIssues.flatMap((i) => i.alternatives))];

  // Đưa focus vào ô nhập khi người dùng chủ động mở một ô để sửa (thay cho `autoFocus`).
  // Callback ổn định nên React chỉ gọi lại khi ô nhập được mount mới, không phải mỗi lần gõ phím.
  const focusOnMount = useCallback((input: HTMLInputElement | null) => {
    input?.focus();
  }, []);

  function showLocal(message: string): void {
    setError({ info: localError(message), retry: null });
  }

  /** Đọc lại item từ backend (nút "Tải lại bản mới nhất"); không gửi lại thao tác ghi nào. */
  async function reloadItem(): Promise<void> {
    setBusy("reload");
    setError(null);
    try {
      onItemChange(await api.getItem(item.item_id));
    } catch (err) {
      setError({ info: toUiError(err), retry: () => void reloadItem() });
    } finally {
      setBusy(null);
    }
  }

  function startEditing(row: number, col: number): void {
    if (!editable) return;
    setError(null);
    setEditing({ row, col, draft: cellText(table, row, col) ?? "" });
  }

  async function save(row: number, col: number, rawText: string): Promise<void> {
    const text = rawText.trim();
    if (text.length === 0) {
      showLocal("Ô không được để trống.");
      return;
    }
    if (text.length > MAX_CELL_TEXT) {
      showLocal(`Ô dài tối đa ${MAX_CELL_TEXT} ký tự.`);
      return;
    }
    const unchanged = text === cellText(table, row, col);
    const hasIssue = (byCell.get(cellKey(row, col)) ?? []).length > 0;
    if (unchanged && !hasIssue) {
      setEditing(null);
      return;
    }
    setBusy("patch");
    setError(null);
    try {
      const updated = await api.patchTable(item.item_id, table.version, [{ row, col, new_text: text }]);
      setEditing(null);
      onItemChange(updated);
    } catch (err) {
      if (err instanceof ApiError && err.info.kind === "version_conflict") {
        setEditing(null);
        try {
          onItemChange(await api.getItem(item.item_id));
          showLocal("Bảng vừa được sửa ở nơi khác. Đã tải lại bản mới nhất, hãy kiểm tra rồi sửa lại.");
        } catch (reloadErr) {
          setError({ info: toUiError(reloadErr), retry: () => void reloadItem() });
        }
      } else {
        setError({ info: toUiError(err), retry: () => void save(row, col, rawText) });
      }
    } finally {
      setBusy(null);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (editing === null) return;
    if (event.key === "Enter") {
      event.preventDefault();
      void save(editing.row, editing.col, editing.draft);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setEditing(null);
      setError(null);
    }
  }

  async function handleApprove(): Promise<void> {
    if (!approvable) return;
    setBusy("approve");
    setError(null);
    try {
      const approved = await api.approveItem(item.item_id);
      onItemChange(approved);
      onApproved(approved);
    } catch (err) {
      setError({ info: toUiError(err), retry: () => void handleApprove() });
    } finally {
      setBusy(null);
    }
  }

  const approveHint = item.approved
    ? "Item đã được duyệt."
    : item.non_cloneable
      ? "Item bị gắn non-cloneable nên không thể duyệt."
      : approvable
        ? "Không còn lỗi BLOCKING."
        : `Còn ${blockingCount} lỗi BLOCKING — sửa các ô viền đỏ trước khi duyệt.`;

  return (
    <section className={styles.auditor} aria-labelledby="table-auditor-title">
      <header className={styles.header}>
        <h2 id="table-auditor-title" className={styles.title}>
          Kiểm duyệt bảng tiến trình
        </h2>
        <span className={styles.status} data-status={item.report?.status ?? "BLOCKED"}>
          {STATUS_LABEL[item.report?.status ?? "BLOCKED"]}
        </span>
        <span className={styles.counts}>
          {blockingCount} chặn · {warningCount} cảnh báo · version {table.version}
        </span>
      </header>

      {tableLevel.length > 0 && (
        <ul className={styles.tableIssues}>
          {keyedIssues(tableLevel).map(({ key, issue }) => (
            <li key={key} data-severity={issue.severity}>
              <strong>{SEVERITY_LABEL[issue.severity]}</strong> {issue.message_vi}
            </li>
          ))}
        </ul>
      )}

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col" className={styles.rowHead}>
                #
              </th>
              {columns.map(({ col, header }) => (
                <th key={col} scope="col">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row}>
                <th scope="row" className={styles.rowHead}>
                  {row + 1}
                </th>
                {columns.map(({ col, header }) => {
                  const issues = byCell.get(cellKey(row, col)) ?? [];
                  const severity = worstSeverity(issues);
                  const cell = table.cells.find((c) => c.row === row && c.col === col);
                  const isEditing = editing !== null && editing.row === row && editing.col === col;
                  const label = `${header}, dòng ${row + 1}`;
                  return (
                    <td key={col} className={cellClassName(severity, cell?.verified_by_human ?? false)}>
                      {isEditing ? (
                        <input
                          className={styles.input}
                          aria-label={`Sửa ${label}`}
                          value={editing.draft}
                          maxLength={MAX_CELL_TEXT}
                          ref={focusOnMount}
                          disabled={busy !== null}
                          onChange={(event) => setEditing({ row, col, draft: event.target.value })}
                          onKeyDown={handleKeyDown}
                        />
                      ) : (
                        <button
                          type="button"
                          className={styles.cellButton}
                          onClick={() => startEditing(row, col)}
                          disabled={!editable}
                          aria-invalid={severity === "blocking"}
                          aria-label={
                            issues.length > 0
                              ? `${label}: ${cell?.text ?? "trống"}. ${issues.map((i) => i.message_vi).join(" ")}`
                              : `${label}: ${cell?.text ?? "trống"}`
                          }
                          title={issues.map((i) => i.message_vi).join("\n") || undefined}
                        >
                          {cell?.text ?? "—"}
                          {cell !== undefined && cell.confidence < 1 && issues.length > 0 && (
                            <span className={styles.confidence}>{Math.round(cell.confidence * 100)}%</span>
                          )}
                        </button>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing !== null && (
        <fieldset className={styles.editorPanel} aria-label="Chi tiết ô đang sửa">
          {editingIssues.length > 0 && (
            <ul className={styles.cellIssues}>
              {keyedIssues(editingIssues).map(({ key, issue }) => (
                <li key={key} data-severity={issue.severity}>
                  <strong>{SEVERITY_LABEL[issue.severity]}</strong> {issue.message_vi}
                </li>
              ))}
            </ul>
          )}
          {alternatives.length > 0 && (
            <div className={styles.alternatives}>
              <span>Hai lượt đọc ra:</span>
              {alternatives.map((alt) => (
                <button
                  key={alt}
                  type="button"
                  className={styles.altButton}
                  disabled={busy !== null}
                  onClick={() => void save(editing.row, editing.col, alt)}
                >
                  Chọn “{alt}”
                </button>
              ))}
            </div>
          )}
          <div className={styles.editorActions}>
            <button
              type="button"
              className={styles.primaryButton}
              disabled={busy !== null}
              onClick={() => void save(editing.row, editing.col, editing.draft)}
            >
              {busy === "patch" ? "Đang lưu…" : "Lưu ô (Enter)"}
            </button>
            <button
              type="button"
              className={styles.secondaryButton}
              disabled={busy !== null}
              onClick={() => {
                setEditing(null);
                setError(null);
              }}
            >
              Huỷ (Esc)
            </button>
          </div>
        </fieldset>
      )}

      {error !== null && (
        <ErrorNotice
          error={error.info}
          onRetry={error.retry ?? undefined}
          onReload={busy === null ? () => void reloadItem() : undefined}
        />
      )}

      <footer className={styles.footer}>
        <p className={styles.hint}>{approveHint}</p>
        <button
          type="button"
          className={styles.approveButton}
          disabled={!approvable || busy !== null}
          onClick={() => void handleApprove()}
        >
          {busy === "approve" ? "Đang duyệt…" : "Approve & Run Simulation"}
        </button>
      </footer>
    </section>
  );
}
