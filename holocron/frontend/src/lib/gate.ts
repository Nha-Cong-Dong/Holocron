import type { ExtractedTable, GateIssue, GateReport, IngestionItem } from "@/types/holocron";

export type CellKey = `${number}:${number}`;

export function cellKey(row: number, col: number): CellKey {
  return `${row}:${col}`;
}

/** Gom issue theo ô; issue không có toạ độ (LaTeX, self-solve) nằm trong `tableLevel`. */
export function groupIssues(report: GateReport | null): {
  byCell: Map<CellKey, GateIssue[]>;
  tableLevel: GateIssue[];
} {
  const byCell = new Map<CellKey, GateIssue[]>();
  const tableLevel: GateIssue[] = [];
  for (const issue of report?.issues ?? []) {
    if (issue.row === null || issue.col === null) {
      tableLevel.push(issue);
      continue;
    }
    const key = cellKey(issue.row, issue.col);
    const bucket = byCell.get(key);
    if (bucket === undefined) {
      byCell.set(key, [issue]);
    } else {
      bucket.push(issue);
    }
  }
  return { byCell, tableLevel };
}

export function rowCount(table: ExtractedTable): number {
  return table.cells.reduce((max, cell) => Math.max(max, cell.row + 1), 0);
}

/** Chỉ số dòng 0..n-1 của bảng; số dòng là định danh trong domain (`cell.row`), dùng làm key ổn định. */
export function rowIndices(table: ExtractedTable): number[] {
  return Array.from({ length: rowCount(table) }, (_, row) => row);
}

/** Cột kèm chỉ số; chỉ số cột là định danh trong domain (`cell.col`), không đổi khi header trùng tên. */
export function tableColumns(table: ExtractedTable): { col: number; header: string }[] {
  return table.headers.map((header, col) => ({ col, header }));
}

/** Gắn key React dựng từ nội dung issue; issue trùng hệt nhau được thêm hậu tố `#n` để key không trùng. */
export function keyedIssues(issues: GateIssue[]): { key: string; issue: GateIssue }[] {
  const seen = new Map<string, number>();
  return issues.map((issue) => {
    const base = `${issue.code}:${issue.row ?? "-"}:${issue.col ?? "-"}:${issue.message_vi}`;
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return { key: count === 0 ? base : `${base}#${count}`, issue };
  });
}

export function cellText(table: ExtractedTable, row: number, col: number): string | null {
  const cell = table.cells.find((c) => c.row === row && c.col === col);
  return cell === undefined ? null : cell.text;
}

export function hasBlocking(report: GateReport | null): boolean {
  return report === null || report.status === "BLOCKED" || report.issues.some((i) => i.severity === "blocking");
}

/** Bất biến I5: chỉ approve khi Gate không còn BLOCKING, item chưa duyệt và không bị gắn non-cloneable. */
export function canApprove(item: IngestionItem): boolean {
  return !item.approved && !item.non_cloneable && !hasBlocking(item.report);
}
