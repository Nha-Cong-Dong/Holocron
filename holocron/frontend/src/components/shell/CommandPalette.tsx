"use client";

import { type KeyboardEvent, useState } from "react";

import { Modal } from "@/components/common/Modal";
import { type Command, filterCommands } from "@/lib/navigation";

import styles from "./CommandPalette.module.css";

export interface PaletteCommand extends Command {
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: PaletteCommand[];
}

const LISTBOX_ID = "command-palette-list";

function optionId(index: number): string {
  return `command-palette-option-${index}`;
}

/**
 * Hộp lệnh `Ctrl+K`: gõ để lọc (không phân biệt dấu), mũi tên lên/xuống để chọn, `Enter` để chạy, `Esc` để đóng.
 * Theo mẫu ARIA combobox + listbox: focus ở ô nhập, phương án đang chọn báo qua `aria-activedescendant`.
 */
export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  return (
    <Modal open={open} onClose={onClose} labelledBy="command-palette-title">
      <PaletteBody onClose={onClose} commands={commands} />
    </Modal>
  );
}

function PaletteBody({ onClose, commands }: Omit<CommandPaletteProps, "open">) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const results = filterCommands(commands, query);
  const activeIndex = Math.min(active, Math.max(results.length - 1, 0));
  const activeCommand = results[activeIndex];

  function run(command: PaletteCommand | undefined): void {
    if (command === undefined) return;
    onClose();
    command.run();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(results.length === 0 ? 0 : (activeIndex + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(results.length === 0 ? 0 : (activeIndex - 1 + results.length) % results.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(activeCommand);
    }
  }

  return (
    <div className={styles.palette}>
      <h2 id="command-palette-title" className="visually-hidden">
        Hộp lệnh
      </h2>
      <input
        className={styles.input}
        type="text"
        role="combobox"
        aria-expanded={results.length > 0}
        aria-controls={LISTBOX_ID}
        aria-autocomplete="list"
        aria-activedescendant={activeCommand === undefined ? undefined : optionId(activeIndex)}
        placeholder="Gõ để tìm màn hình hoặc lệnh…"
        aria-label="Tìm lệnh"
        aria-describedby="command-palette-count"
        autoComplete="off"
        spellCheck={false}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={handleKeyDown}
      />
      <p id="command-palette-count" className="visually-hidden" aria-live="polite">
        {results.length} lệnh phù hợp
      </p>
      {results.length === 0 && (
        <p className={styles.empty}>Không có lệnh nào khớp. Thử từ khác, ví dụ "đề thi" hoặc "phím tắt".</p>
      )}
      {/* Listbox luôn có mặt để `aria-controls` luôn trỏ tới phần tử tồn tại; focus ở lại ô nhập. */}
      <div
        id={LISTBOX_ID}
        role="listbox"
        aria-label="Lệnh phù hợp"
        className={styles.list}
        hidden={results.length === 0}
      >
        {results.map((command, index) => (
          <div
            key={command.id}
            id={optionId(index)}
            role="option"
            tabIndex={-1}
            aria-selected={index === activeIndex}
            className={styles.command}
            data-active={index === activeIndex}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => run(command)}
            onKeyDown={(event) => {
              if (event.key === "Enter") run(command);
            }}
            onMouseEnter={() => setActive(index)}
          >
            <span className={styles.label}>{command.label}</span>
            <span className={styles.hint}>{command.hint}</span>
          </div>
        ))}
      </div>
      <p className={styles.footer}>
        <kbd>↑</kbd> <kbd>↓</kbd> chọn · <kbd>Enter</kbd> chạy · <kbd>Esc</kbd> đóng
      </p>
    </div>
  );
}
