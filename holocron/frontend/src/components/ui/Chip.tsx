"use client";

import { type ReactNode, useState } from "react";

import styles from "./Chip.module.css";

const STAR_PATH = "M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z";

interface ChipBase {
  children: ReactNode;
  className?: string;
  disabled?: boolean;
}

/** Chip bật/tắt độc lập (ví dụ chọn nhiều chương): `<button aria-pressed>`. */
export interface ToggleChipProps extends ChipBase {
  kind: "toggle";
  pressed: boolean;
  onToggle: (next: boolean) => void;
}

/** Chip chọn một trong nhóm (ví dụ 5/10/20 câu, giao diện): `<input type="radio">` thật nằm trong nhãn. */
export interface RadioChipProps extends ChipBase {
  kind: "radio";
  name: string;
  value: string;
  checked: boolean;
  onSelect: (value: string) => void;
}

export type ChipProps = ToggleChipProps | RadioChipProps;

/** Lần nảy kế tiếp: đổi qua lại 1 ↔ 2 để CSS chạy lại animation mà không cần hẹn giờ. */
type Pop = 0 | 1 | 2;

function nextPop(pop: Pop): Pop {
  return pop === 1 ? 2 : 1;
}

function Decorations({ children }: { children: ReactNode }) {
  return (
    <>
      <svg className={`m-chip-check ${styles.check}`} viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 12.5l4 4 8-9" />
      </svg>
      {children}
      <svg className="m-chip-star" viewBox="0 0 24 24" aria-hidden="true">
        <path d={STAR_PATH} fill="currentColor" />
      </svg>
    </>
  );
}

/**
 * Chip chọn: dấu tích phóng ra, nảy nhẹ mỗi lần bấm, sao xoay vào góc khi được chọn. Hai dạng: bật/tắt
 * (`aria-pressed`) và radio (input thật, mũi tên đổi lựa chọn trong nhóm như radio gốc).
 */
export function Chip(props: ChipProps) {
  const [pop, setPop] = useState<Pop>(0);
  const selected = props.kind === "toggle" ? props.pressed : props.checked;
  const className = [
    "m-chip",
    styles.chip,
    selected ? `is-selected ${styles.selected}` : "",
    props.disabled === true ? styles.disabled : "",
    props.className ?? "",
  ]
    .filter((name) => name !== "")
    .join(" ");
  const dataPop = pop === 0 ? undefined : String(pop);

  if (props.kind === "toggle") {
    const { pressed, onToggle } = props;
    return (
      <button
        type="button"
        className={className}
        data-pop={dataPop}
        aria-pressed={pressed}
        disabled={props.disabled}
        onClick={() => {
          setPop(nextPop);
          onToggle(!pressed);
        }}
      >
        <Decorations>{props.children}</Decorations>
      </button>
    );
  }

  const { name, value, checked, onSelect } = props;
  return (
    <label className={className} data-pop={dataPop}>
      <input
        type="radio"
        className={styles.radio}
        name={name}
        value={value}
        checked={checked}
        disabled={props.disabled}
        onChange={() => {
          setPop(nextPop);
          onSelect(value);
        }}
      />
      <Decorations>{props.children}</Decorations>
    </label>
  );
}
