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
