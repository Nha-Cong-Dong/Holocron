"use client";

import { type InputHTMLAttributes, useId } from "react";

import styles from "./Field.module.css";

type NativeInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "className" | "placeholder" | "aria-describedby" | "aria-invalid" | "children"
>;

export interface FieldProps extends NativeInputProps {
  /** Nhãn nằm trong ô khi trống, bay lên khi focus hoặc đã có chữ. */
  label: string;
  /** Một dòng nói việc cần làm. */
  hint?: string;
  /** Lỗi của ô: việc gì sai và cách sửa. Có lỗi thì ô có `aria-invalid`. */
  error?: string;
  className?: string;
}

/**
 * Ô nhập kiểu gạch chân (không khung): nhãn bay lên, vạch dưới mọc từ trái sang khi focus. Gợi ý và lỗi nối vào ô bằng
 * `aria-describedby`; có lỗi thì `aria-invalid` và vạch đổi màu lỗi.
 */
export function Field({ label, hint, error, className, id, ...input }: FieldProps) {
  const autoId = useId();
  const inputId = id ?? `${autoId}-input`;
  const hintId = `${autoId}-hint`;
  const errorId = `${autoId}-error`;
  const invalid = error !== undefined && error.trim() !== "";
  const describedBy = [hint !== undefined ? hintId : "", invalid ? errorId : ""]
    .filter((part) => part !== "")
    .join(" ");
  return (
    <div className={[styles.root, className ?? ""].filter((part) => part !== "").join(" ")}>
      <div className={`m-field ${styles.box}`} data-invalid={invalid ? "true" : undefined}>
        <input
          {...input}
          id={inputId}
          className={styles.input}
          placeholder=" "
          aria-invalid={invalid || undefined}
          aria-describedby={describedBy === "" ? undefined : describedBy}
        />
        <label htmlFor={inputId} className={`m-field-label ${styles.label}`}>
          {label}
        </label>
      </div>
      {hint !== undefined && (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      )}
      {invalid && (
        <p id={errorId} className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}
