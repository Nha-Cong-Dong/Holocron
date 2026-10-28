"use client";

import { type KeyboardEvent, type ReactNode, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/common/Icon";
import { type PaneDirection, paneDirection, tabIndexForKey } from "@/lib/tabs";

import styles from "./Tabs.module.css";

export interface TabItem<Id extends string> {
  id: Id;
  label: string;
  icon?: IconName;
}

interface TabsProps<Id extends string> {
  /** Tên của hàng tab cho trình đọc màn hình, ví dụ "Phần của môn". */
  label: string;
  items: readonly TabItem<Id>[];
  selected: Id;
  onSelect: (id: Id) => void;
  /** Tiền tố id của tab và vùng nội dung; duy nhất trong trang. */
  idPrefix: string;
  /** Nội dung của tab đang chọn. */
  children: ReactNode;
}

/**
 * Hàng tab theo mẫu ARIA tabs (design-handoff/README.md màn 3): chỉ tab đang chọn có `tabIndex=0`; ←/→ đi vòng,
 * Home/End về đầu/cuối, chọn ngay khi chuyển focus. Vạch dưới `m-tab-indicator` trượt theo tab; nội dung vào bằng
 * `m-pane-r`/`m-pane-l` theo hướng đổi tab (không animate ở lần vẽ đầu). Giảm chuyển động do `motion.css` xử lý.
 */
export function Tabs<Id extends string>({ label, items, selected, onSelect, idPrefix, children }: TabsProps<Id>) {
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const selectedIndex = Math.max(
    items.findIndex((item) => item.id === selected),
    0,
  );

  // Hướng trượt suy ra từ lần đổi chỉ số gần nhất (mẫu "điều chỉnh state khi prop đổi" của React, không dùng effect).
  const [motion, setMotion] = useState<{ index: number; direction: PaneDirection | null }>({
    index: selectedIndex,
    direction: null,
  });
  if (motion.index !== selectedIndex) {
    setMotion({ index: selectedIndex, direction: paneDirection(motion.index, selectedIndex) });
  }

  const tabId = (index: number) => `${idPrefix}-tab-${index}`;
  const panelId = `${idPrefix}-panel`;

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const focused = tabRefs.current.findIndex((element) => element !== null && element === event.target);
    const next = tabIndexForKey(event.key, focused === -1 ? selectedIndex : focused, items.length);
    const item = next === null ? undefined : items[next];
    if (next === null || item === undefined) return;
    event.preventDefault();
    tabRefs.current[next]?.focus();
    if (item.id !== selected) onSelect(item.id);
  }

  const paneClass = motion.direction === null ? undefined : `m-pane-${motion.direction}`;

  return (
    <div className={styles.root}>
      <div className={styles.list} role="tablist" aria-label={label} onKeyDown={onKeyDown}>
        {items.map((item, index) => {
          const isSelected = index === selectedIndex;
          return (
            <button
              key={item.id}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={tabId(index)}
              className={styles.tab}
              aria-selected={isSelected}
              aria-controls={panelId}
              tabIndex={isSelected ? 0 : -1}
              onClick={() => {
                if (!isSelected) onSelect(item.id);
              }}
            >
              {item.icon !== undefined && <Icon name={item.icon} size={20} />}
              {item.label}
            </button>
          );
        })}
        <span
          className={`m-tab-indicator ${styles.indicator}`}
          aria-hidden="true"
          style={{ transform: `translateX(${selectedIndex * 100}%)` }}
        />
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={tabId(selectedIndex)} className={styles.panel}>
        <div key={selected} className={paneClass}>
          {children}
        </div>
      </div>
    </div>
  );
}
