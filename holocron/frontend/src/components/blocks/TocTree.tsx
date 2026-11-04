"use client";

import { useEffect, useRef } from "react";

import { type TocNode, type TocRow, treeKey } from "@/lib/toc";

import styles from "./Blocks.module.css";

interface TocTreeProps {
  roots: readonly TocNode[];
  rows: readonly TocRow[];
  /** Hàng đang giữ focus bàn phím (roving tabindex). */
  focusedId: string;
  selectedId: string | null;
  onFocusChange: (id: string) => void;
  onToggle: (id: string, open: boolean) => void;
  onSelect: (node: TocNode) => void;
}

function pagesLabel(node: TocNode): string {
  if (node.pageStart === null || node.pageEnd === null) return "";
  return node.pageStart === node.pageEnd ? `tr. ${node.pageStart}` : `tr. ${node.pageStart}–${node.pageEnd}`;
}

/** Mục lục dạng ARIA tree: mũi tên di chuyển, mở/đóng; Enter chọn mục để xem khối của nó. */
export function TocTree({ roots, rows, focusedId, selectedId, onFocusChange, onToggle, onSelect }: TocTreeProps) {
  const refs = useRef(new Map<string, HTMLDivElement>());
  const moveFocus = useRef(false);

  useEffect(() => {
    if (!moveFocus.current) return;
    moveFocus.current = false;
    refs.current.get(focusedId)?.focus();
  }, [focusedId]);

  return (
    <div role="tree" aria-label="Mục lục của môn" className={styles.tree}>
      {rows.map((row) => {
        const { node } = row;
        return (
          <div
            key={node.id}
            role="treeitem"
            aria-level={row.level}
            aria-posinset={row.posInSet}
            aria-setsize={row.setSize}
            aria-expanded={row.expanded === null ? undefined : row.expanded}
            aria-selected={node.id === selectedId}
            tabIndex={node.id === focusedId ? 0 : -1}
            ref={(element) => {
              if (element === null) refs.current.delete(node.id);
              else refs.current.set(node.id, element);
            }}
            className={node.id === selectedId ? `${styles.treeItem} ${styles.treeSelected}` : styles.treeItem}
            style={{ paddingInlineStart: `calc(var(--sp-3) * ${row.level})` }}
            onFocus={(event) => {
              if (event.target === event.currentTarget) onFocusChange(node.id);
            }}
            onClick={(event) => {
              event.stopPropagation();
              onFocusChange(node.id);
              onSelect(node);
            }}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget) return;
              const result = treeKey(roots, rows, node.id, event.key);
              if (result === null) return;
              event.preventDefault();
              event.stopPropagation();
              if (result.kind === "focus") {
                moveFocus.current = true;
                onFocusChange(result.id);
              } else if (result.kind === "toggle") {
                onToggle(result.id, result.open);
              } else {
                onSelect(node);
              }
            }}
          >
            <span className={styles.treeMark} aria-hidden="true">
              {row.expanded === null ? "·" : row.expanded ? "▾" : "▸"}
            </span>
            <span className={styles.treeLabel}>{node.label}</span>
            {node.warning !== null && (
              <span className={styles.treeWarn} title={node.warning}>
                <span aria-hidden="true">⚠ </span>
                <span className={styles.srOnly}>Cảnh báo: {node.warning}</span>
              </span>
            )}
            <span className={styles.treePages}>{pagesLabel(node)}</span>
          </div>
        );
      })}
    </div>
  );
}
