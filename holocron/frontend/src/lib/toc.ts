/**
 * Mục lục của môn (`GET /v1/vault/tree`, §6.10): dựng cây Nguồn → Chương → Mục thành danh sách hàng phẳng cho ARIA tree,
 * phím mũi tên và việc nhớ vị trí. Hàm thuần; mọi số (số trang) do backend trả, không tính lại ở đây.
 */

import type { KeyValueStorage } from "@/lib/theme";
import type { SectionNode, SubjectTree } from "@/types/holocron";

export type TocKind = "source" | "chapter" | "section";

export interface TocNode {
  /** Khoá duy nhất trong cây: `doc:<id>` cho nguồn, `sec:<id>` cho mục. */
  id: string;
  kind: TocKind;
  label: string;
  documentId: string;
  /** `null` với nguồn (chưa chọn mục cụ thể). */
  sectionId: string | null;
  pageStart: number | null;
  pageEnd: number | null;
  children: TocNode[];
}

export interface TocRow {
  node: TocNode;
  /** 1 là nguồn. */
  level: number;
  posInSet: number;
  setSize: number;
  /** `null` khi nút là lá. */
  expanded: boolean | null;
}

const UNTITLED = "(Chưa có tiêu đề)";

function sectionNode(node: SectionNode, documentId: string): TocNode {
  const { section } = node;
  return {
    id: `sec:${section.section_id}`,
    kind: section.kind,
    label: section.title !== null && section.title.trim() !== "" ? section.title : UNTITLED,
    documentId,
    sectionId: section.section_id,
    pageStart: section.page_start,
    pageEnd: section.page_end,
    children: node.children.map((child) => sectionNode(child, documentId)),
  };
}

/** Cây của môn thành các nút nguồn; nguồn chưa có cây mục (`root` null) là nút lá. */
export function buildToc(tree: SubjectTree): TocNode[] {
  return tree.sources.map((source) => ({
    id: `doc:${source.document_id}`,
    kind: "source",
    label: source.title,
    documentId: source.document_id,
    sectionId: null,
    pageStart: null,
    pageEnd: null,
    children: source.root === null ? [] : [sectionNode(source.root, source.document_id)],
  }));
}

/** Các hàng đang hiện theo tập nút đã mở. */
export function visibleRows(roots: readonly TocNode[], expanded: ReadonlySet<string>): TocRow[] {
  const rows: TocRow[] = [];
  const walk = (nodes: readonly TocNode[], level: number): void => {
    nodes.forEach((node, index) => {
      const hasChildren = node.children.length > 0;
      const open = hasChildren && expanded.has(node.id);
      rows.push({ node, level, posInSet: index + 1, setSize: nodes.length, expanded: hasChildren ? open : null });
      if (open) walk(node.children, level + 1);
    });
  };
  walk(roots, 1);
  return rows;
}

function parentIdOf(roots: readonly TocNode[], id: string): string | null {
  let found: string | null = null;
  const walk = (nodes: readonly TocNode[], parent: string | null): void => {
    for (const node of nodes) {
      if (node.id === id) found = parent;
      walk(node.children, node.id);
    }
  };
  walk(roots, null);
  return found;
}

export type TocKeyResult =
  | { kind: "focus"; id: string }
  | { kind: "toggle"; id: string; open: boolean }
  | { kind: "select"; id: string }
  | null;

/** Phím trên một hàng theo mẫu WAI-ARIA tree: ↑ ↓ Home End di chuyển, → mở/vào con, ← đóng/về cha, Enter chọn. */
export function treeKey(
  roots: readonly TocNode[],
  rows: readonly TocRow[],
  currentId: string,
  key: string,
): TocKeyResult {
  const index = rows.findIndex((row) => row.node.id === currentId);
  const row = rows[index];
  if (row === undefined) return null;
  switch (key) {
    case "ArrowDown": {
      const next = rows[index + 1];
      return next === undefined ? null : { kind: "focus", id: next.node.id };
    }
    case "ArrowUp": {
      const previous = rows[index - 1];
      return previous === undefined ? null : { kind: "focus", id: previous.node.id };
    }
    case "Home": {
      const first = rows[0];
      return first === undefined ? null : { kind: "focus", id: first.node.id };
    }
    case "End": {
      const last = rows[rows.length - 1];
      return last === undefined ? null : { kind: "focus", id: last.node.id };
    }
    case "ArrowRight": {
      if (row.expanded === null) return null;
      if (!row.expanded) return { kind: "toggle", id: row.node.id, open: true };
      const child = rows[index + 1];
      return child === undefined ? null : { kind: "focus", id: child.node.id };
    }
    case "ArrowLeft": {
      if (row.expanded === true) return { kind: "toggle", id: row.node.id, open: false };
      const parent = parentIdOf(roots, row.node.id);
      return parent === null ? null : { kind: "focus", id: parent };
    }
    case "Enter":
    case " ":
      return { kind: "select", id: row.node.id };
    default:
      return null;
  }
}

/** Đường dẫn chữ từ nguồn tới nút (cho nhãn "đang xem"). */
export function pathLabels(roots: readonly TocNode[], id: string): string[] {
  const walk = (nodes: readonly TocNode[], trail: string[]): string[] | null => {
    for (const node of nodes) {
      const next = [...trail, node.label];
      if (node.id === id) return next;
      const inner = walk(node.children, next);
      if (inner !== null) return inner;
    }
    return null;
  };
  return walk(roots, []) ?? [];
}

// ------------------------------------------------------------------------------------------ giữ vị trí

export interface TocPosition {
  expanded: string[];
  selected: string | null;
}

const STORAGE_PREFIX = "holocron.toc.";

export function readTocPosition(storage: KeyValueStorage | null, subjectCode: string): TocPosition {
  const empty: TocPosition = { expanded: [], selected: null };
  if (storage === null) return empty;
  try {
    const raw = storage.getItem(`${STORAGE_PREFIX}${subjectCode}`);
    if (raw === null) return empty;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return empty;
    const expanded: unknown = Reflect.get(value, "expanded");
    const selected: unknown = Reflect.get(value, "selected");
    return {
      expanded: Array.isArray(expanded) ? expanded.filter((item): item is string => typeof item === "string") : [],
      selected: typeof selected === "string" ? selected : null,
    };
  } catch {
    return empty;
  }
}

export function writeTocPosition(storage: KeyValueStorage | null, subjectCode: string, position: TocPosition): void {
  if (storage === null) return;
  try {
    storage.setItem(`${STORAGE_PREFIX}${subjectCode}`, JSON.stringify(position));
  } catch {
    // Kho đầy hoặc bị chặn: mục lục vẫn dùng được, chỉ không nhớ vị trí.
  }
}
