"use client";

import { type KeyboardEvent, type MouseEvent, useEffect, useId, useRef, useState } from "react";

import { type DropdownKeyAction, dropdownKeyAction } from "@/lib/dropdown-keys";

import styles from "./Dropdown.module.css";

export interface DropdownOption<Value extends string> {
  value: Value;
  label: string;
}

export interface DropdownProps<Value extends string> {
  /** Nhãn nhìn thấy phía trên nút mở. */
  label: string;
  options: readonly DropdownOption<Value>[];
  value: Value;
  onChange: (value: Value) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Bảng thả xuống (listbox tuỳ biến, mẫu "select-only combobox" của ARIA APG). Nút mở có `aria-haspopup="listbox"` và
 * `aria-expanded`; khi mở, focus vào `role="listbox"`, dòng đang trỏ báo bằng `aria-activedescendant`. Phím: ↑/↓,
 * Home/End, Enter/Space chọn, Esc đóng (logic ở `lib/dropdown-keys.ts`). Bấm ra ngoài thì đóng. Bảng lật xuống như
 * mặt khối mở ra, các dòng hiện lần lượt.
 */
export function Dropdown<Value extends string>({
  label,
  options,
  value,
  onChange,
  disabled,
  className,
}: DropdownProps<Value>) {
  const baseId = useId();
  const labelId = `${baseId}-label`;
  const buttonId = `${baseId}-button`;
  const listId = `${baseId}-list`;
  const optionId = (index: number) => `${baseId}-option-${index}`;

  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const selectedIndex = options.findIndex((option) => option.value === value);
  const selected = selectedIndex === -1 ? undefined : options[selectedIndex];

  // Bảng vừa mở: focus vào listbox để phím đi tới bảng (focus nằm trong component, không lấy của nơi khác).
  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  // Bấm ra ngoài thì đóng (không kéo focus về nút: người dùng đang bấm vào chỗ khác).
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent): void {
      const root = rootRef.current;
      if (root !== null && event.target instanceof Node && !root.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  function apply(action: DropdownKeyAction): void {
    switch (action.type) {
      case "open":
        setActive(action.active);
        setOpen(true);
        return;
      case "move":
        setActive(action.active);
        document.getElementById(optionId(action.active))?.scrollIntoView({ block: "nearest" });
        return;
      case "select": {
        const option = options[action.index];
        setOpen(false);
        buttonRef.current?.focus();
        if (option !== undefined && option.value !== value) onChange(option.value);
        return;
      }
      case "close":
        setOpen(false);
        if (action.restoreFocus) buttonRef.current?.focus();
        return;
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const action = dropdownKeyAction(event.key, { open, active, selected: selectedIndex, count: options.length });
    if (action === null) return;
    // Tab để trình duyệt chuyển focus tiếp; các phím còn lại không cuộn trang hay bấm lại nút.
    if (event.key !== "Tab") event.preventDefault();
    apply(action);
  }

  function onListClick(event: MouseEvent<HTMLDivElement>): void {
    const target = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-index]") : null;
    const index = Number(target?.dataset.index);
    if (Number.isInteger(index)) apply({ type: "select", index });
  }

  return (
    <div ref={rootRef} className={[styles.root, className ?? ""].filter((part) => part !== "").join(" ")}>
      <span id={labelId} className={styles.label}>
        {label}
      </span>
      <button
        ref={buttonRef}
        id={buttonId}
        type="button"
        className={styles.button}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-labelledby={`${labelId} ${buttonId}`}
        disabled={disabled}
        onClick={() =>
          open
            ? apply({ type: "close", restoreFocus: false })
            : apply({ type: "open", active: Math.max(selectedIndex, 0) })
        }
        onKeyDown={onKeyDown}
      >
        <span className={styles.value}>{selected?.label ?? "Chọn…"}</span>
        <svg className={`m-dropdown-chevron ${styles.chevron}`} viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-labelledby={labelId}
          aria-activedescendant={active >= 0 && active < options.length ? optionId(active) : undefined}
          className={`m-dropdown-panel ${styles.panel}`}
          onKeyDown={onKeyDown}
          onClick={onListClick}
          onBlur={(event) => {
            const next = event.relatedTarget;
            if (!(next instanceof Node) || !(rootRef.current?.contains(next) ?? false)) setOpen(false);
          }}
        >
          {options.map((option, index) => (
            <div
              key={option.value}
              id={optionId(index)}
              role="option"
              tabIndex={-1}
              aria-selected={index === selectedIndex}
              data-index={index}
              data-active={index === active ? "true" : undefined}
              className={`m-dropdown-option ${styles.option}`}
            >
              {option.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
