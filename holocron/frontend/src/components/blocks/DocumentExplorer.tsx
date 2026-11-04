"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Chip, ErrorState } from "@/components/ui";
import { api } from "@/lib/api";
import { toUiError, type UiError } from "@/lib/errors";
import { FILTER_TYPES, groupHits, toggleType, unsearchedNotice } from "@/lib/search-results";
import { GLOBAL_SCOPE, isSearchShortcut, targetKindOf } from "@/lib/shortcuts";
import { browserStorage } from "@/lib/theme";
import { buildToc, pathLabels, readTocPosition, type TocNode, visibleRows, writeTocPosition } from "@/lib/toc";
import type { BlockSearchPage, BlockType, SectionBlocks, SubjectTree } from "@/types/holocron";

import { BlockCard } from "./BlockCard";
import styles from "./Blocks.module.css";
import { TocTree } from "./TocTree";

const DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 20;
const SECTION_LIMIT = 200;

type Load<T> = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; value: T };

function failure(error: unknown): { kind: "error"; error: UiError } {
  return { kind: "error", error: toUiError(error) };
}

/**
 * Tải dữ liệu bất đồng bộ: `load` null ⇒ không tải (`state` null). Đổi `load` hoặc gọi `reload` thì tải lại; kết quả
 * của lần tải cũ bị bỏ. Người gọi phải bọc `load` bằng `useCallback`.
 */
function useLoad<T>(load: (() => Promise<T>) | null): { state: Load<T> | null; reload: () => void } {
  const [state, setState] = useState<Load<T> | null>(load === null ? null : { kind: "loading" });
  const sequence = useRef(0);
  const run = useCallback(() => {
    if (load === null) {
      sequence.current += 1;
      setState(null);
      return;
    }
    sequence.current += 1;
    const mine = sequence.current;
    setState({ kind: "loading" });
    load().then(
      (value) => {
        if (sequence.current === mine) setState({ kind: "ready", value });
      },
      (error: unknown) => {
        if (sequence.current === mine) setState(failure(error));
      },
    );
  }, [load]);
  useEffect(() => {
    run();
    return () => {
      sequence.current += 1;
    };
  }, [run]);
  return { state, reload: run };
}

interface DocumentExplorerProps {
  subjectCode: string;
}

/**
 * Mục lục + tra cứu khối của môn (ADR-010, §6.10). Ô tra cứu luôn hiện (phím `/`), lọc theo loại khối, debounce; cây
 * Nguồn → Chương → Mục mở/đóng bằng bàn phím và nhớ vị trí. Mọi dữ liệu do backend trả; không tính lại số nào.
 */
export function DocumentExplorer({ subjectCode }: DocumentExplorerProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(readTocPosition(browserStorage(), subjectCode).expanded),
  );
  const [selectedId, setSelectedId] = useState<string | null>(
    () => readTocPosition(browserStorage(), subjectCode).selected,
  );
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [types, setTypes] = useState<readonly BlockType[]>([]);
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const loadTree = useCallback(() => api.getSubjectTree(subjectCode), [subjectCode]);
  const { state: treeState, reload: reloadTree } = useLoad(loadTree);
  const tree: Load<SubjectTree> = treeState ?? { kind: "loading" };

  const roots = useMemo(() => (tree.kind === "ready" ? buildToc(tree.value) : []), [tree]);
  const rows = useMemo(() => visibleRows(roots, expanded), [roots, expanded]);
  const selectedNode = useMemo(() => {
    const walk = (nodes: readonly TocNode[]): TocNode | null => {
      for (const node of nodes) {
        if (node.id === selectedId) return node;
        const inner = walk(node.children);
        if (inner !== null) return inner;
      }
      return null;
    };
    return walk(roots);
  }, [roots, selectedId]);

  // Giữ vị trí: ghi khi mở/đóng hoặc chọn.
  useEffect(() => {
    writeTocPosition(browserStorage(), subjectCode, { expanded: [...expanded], selected: selectedId });
  }, [subjectCode, expanded, selectedId]);

  // Khối của mục đang chọn.
  const selectedDocumentId = selectedNode?.documentId ?? null;
  const selectedSectionId = selectedNode?.sectionId ?? null;
  const loadSection = useMemo(
    () =>
      selectedDocumentId === null || selectedSectionId === null
        ? null
        : () => api.getSectionBlocks(selectedDocumentId, selectedSectionId, 0, SECTION_LIMIT),
    [selectedDocumentId, selectedSectionId],
  );
  const { state: section, reload: reloadSection } = useLoad<SectionBlocks>(loadSection);

  // Tra cứu có debounce: gõ xong DEBOUNCE_MS mới gửi.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);
  const loadSearch = useMemo(
    () =>
      debouncedQuery === ""
        ? null
        : () => api.searchBlocks({ q: debouncedQuery, subjectCode, types, offset: 0, limit: SEARCH_LIMIT }),
    [debouncedQuery, subjectCode, types],
  );
  const { state: searchState, reload: reloadSearch } = useLoad<BlockSearchPage>(loadSearch);
  // Ô trống: bỏ kết quả ngay, không chờ debounce.
  const search = query.trim() === "" ? null : searchState;

  // Phím tắt `/` nhảy tới ô tra cứu.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      const target = event.target;
      const matched = isSearchShortcut(
        {
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          altKey: event.altKey,
          target: targetKindOf(
            target instanceof HTMLElement
              ? {
                  tagName: target.tagName,
                  isContentEditable: target.isContentEditable,
                  inputType: target instanceof HTMLInputElement ? target.type : null,
                }
              : null,
          ),
        },
        { overlayOpen: document.querySelector("dialog[open]") !== null, scope: GLOBAL_SCOPE },
      );
      if (matched) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const onToggle = useCallback((id: string, open: boolean) => {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const onSelect = useCallback(
    (node: TocNode) => {
      if (node.children.length > 0) onToggle(node.id, !expanded.has(node.id));
      if (node.sectionId !== null) setSelectedId(node.id);
    },
    [expanded, onToggle],
  );

  const currentFocus =
    focusedId !== null && rows.some((row) => row.node.id === focusedId) ? focusedId : (rows[0]?.node.id ?? null);

  const groups = useMemo(() => (search?.kind === "ready" ? groupHits(search.value.hits) : []), [search]);

  return (
    <div className={styles.explorer}>
      <div className={styles.searchBar}>
        <label className={styles.searchLabel} htmlFor="blocks-search">
          Tra cứu trong tài liệu <kbd className={styles.kbd}>/</kbd>
        </label>
        <input
          id="blocks-search"
          ref={inputRef}
          type="search"
          className={styles.searchInput}
          value={query}
          maxLength={200}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Gõ từ khoá, ví dụ tên khái niệm hoặc một dòng code"
          autoComplete="off"
        />
        <fieldset className={styles.filters}>
          <legend className="visually-hidden">Lọc theo loại khối</legend>
          {FILTER_TYPES.map((entry) => (
            <Chip
              key={entry.type}
              kind="toggle"
              pressed={types.includes(entry.type)}
              onToggle={() => setTypes((previous) => toggleType(previous, entry.type))}
            >
              {entry.label}
            </Chip>
          ))}
        </fieldset>
      </div>

      {search !== null && (
        <section className={styles.results} aria-label="Kết quả tra cứu" aria-busy={search.kind === "loading"}>
          {search.kind === "loading" && <p className={styles.note}>Đang tra cứu…</p>}
          {search.kind === "error" && (
            <ErrorState
              title="Không tra cứu được"
              fix="Kiểm tra từ khoá (1–200 ký tự) rồi bấm Thử lại. Nếu vẫn lỗi, mở lại Holocron."
              onRetry={reloadSearch}
              technical={search.error.diagnostic}
            />
          )}
          {search.kind === "ready" && unsearchedNotice(search.value) !== null && (
            <p className={styles.warnNote} role="status">
              {unsearchedNotice(search.value)}
            </p>
          )}
          {search.kind === "ready" && search.value.hits.length === 0 && (
            <p className={styles.note}>
              Không thấy khối nào khớp. Thử từ khoá ngắn hơn, bỏ bớt bộ lọc loại khối, hoặc kiểm tra các trang chưa đọc
              được.
            </p>
          )}
          {search.kind === "ready" && search.value.hits.length > 0 && (
            <>
              <p className={styles.note} aria-live="polite">
                {search.value.total_capped ? `Hơn ${search.value.total}` : search.value.total} kết quả
                {search.value.hits.length < search.value.total ? `, hiện ${search.value.hits.length} đầu` : ""}.
              </p>
              {groups.map((group) => (
                <section key={group.key} className={styles.group}>
                  <h3 className={styles.groupTitle}>
                    {group.documentTitle} › {group.path}
                  </h3>
                  {group.hits.map((hit) => (
                    <BlockCard key={hit.block.block_id} block={hit.block} spans={hit.match_spans} />
                  ))}
                </section>
              ))}
            </>
          )}
        </section>
      )}

      <div className={styles.toc}>
        <h3 className={styles.tocTitle}>Mục lục</h3>
        {tree.kind === "loading" && <p className={styles.note}>Đang mở mục lục…</p>}
        {tree.kind === "error" && (
          <ErrorState
            title="Không mở được mục lục"
            fix="Holocron chưa trả lời. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron."
            onRetry={reloadTree}
            technical={tree.error.diagnostic}
          />
        )}
        {tree.kind === "ready" && roots.length === 0 && (
          <p className={styles.note}>
            Chưa có mục lục. Mục lục xuất hiện sau khi một tài liệu được đọc xong và xác nhận.
          </p>
        )}
        {tree.kind === "ready" && roots.length > 0 && currentFocus !== null && (
          <TocTree
            roots={roots}
            rows={rows}
            focusedId={currentFocus}
            selectedId={selectedId}
            onFocusChange={setFocusedId}
            onToggle={onToggle}
            onSelect={onSelect}
          />
        )}
        {section !== null && (
          <section
            className={styles.sectionBlocks}
            aria-label="Nội dung mục đang chọn"
            aria-busy={section.kind === "loading"}
          >
            {selectedId !== null && <p className={styles.note}>{pathLabels(roots, selectedId).join(" › ")}</p>}
            {section.kind === "loading" && <p className={styles.note}>Đang mở các khối của mục…</p>}
            {section.kind === "error" && (
              <ErrorState
                title="Không mở được nội dung mục"
                fix="Bấm chọn lại mục trong mục lục. Nếu vẫn lỗi, mở lại Holocron."
                onRetry={reloadSection}
                technical={section.error.diagnostic}
              />
            )}
            {section.kind === "ready" && section.value.blocks.length === 0 && (
              <p className={styles.note}>Mục này chưa có khối nào đủ điều kiện hiển thị.</p>
            )}
            {section.kind === "ready" &&
              section.value.blocks.map((block) => <BlockCard key={block.block_id} block={block} />)}
            {section.kind === "ready" && section.value.blocks.length < section.value.total && (
              <p className={styles.note}>
                Hiện {section.value.blocks.length} trên {section.value.total} khối của mục.
              </p>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
