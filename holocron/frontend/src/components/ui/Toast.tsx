"use client";

import {
  type AnimationEvent,
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";

import { useReducedMotion } from "@/hooks/motion-hooks";
import { EMPTY_TOAST_QUEUE, TOAST_DURATION_MS, toastReducer } from "@/lib/toast-queue";

import { Button } from "./Button";
import styles from "./Toast.module.css";

/** Hết giờ dự phòng cho animation rời đi (animation thật dài `--d-short`); hết giờ thì gỡ dù không nhận `animationend`. */
const LEAVE_FALLBACK_MS = 400;

const STAR_PATH = "M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z";

export interface ToastApi {
  /** Hiện một thông báo ngắn, ví dụ "Đã nhận 6 khái niệm". Tự ẩn sau vài giây. */
  show: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Gọi trong component nằm dưới `ToastProvider` (khung app đã mount sẵn). */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (api === null) throw new Error("useToast cần ToastProvider ở phía trên cây component.");
  return api;
}

export interface ToastProps {
  message: string;
  leaving: boolean;
  onClose: () => void;
  /** Animation rời đi đã xong: gỡ thông báo. */
  onLeft: () => void;
}

/** Một thông báo: rơi từ trên xuống, ngôi sao xoay một vòng; nút đóng; rời đi bằng cách lướt sang phải. */
export function Toast({ message, leaving, onClose, onLeft }: ToastProps) {
  function onAnimationEnd(event: AnimationEvent<HTMLDivElement>): void {
    if (leaving && event.target === event.currentTarget && event.animationName === "m-toast-out") onLeft();
  }
  return (
    <div className={`m-toast ${styles.toast}${leaving ? " is-leaving" : ""}`} onAnimationEnd={onAnimationEnd}>
      <svg className={`m-toast-star ${styles.star}`} viewBox="0 0 24 24" aria-hidden="true">
        <path d={STAR_PATH} fill="currentColor" />
      </svg>
      <span className={styles.message}>{message}</span>
      <Button variant="icon" aria-label="Đóng thông báo" className={styles.close} onClick={onClose}>
        <svg className={styles.closeIcon} viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </Button>
    </div>
  );
}

/**
 * Hàng đợi thông báo nhanh cho toàn app (hàng đợi thuần ở `lib/toast-queue.ts`). Vùng hiển thị luôn có trong DOM với
 * `aria-live="polite"` để trình đọc màn hình đọc thông báo mới. Mỗi thông báo tự rời đi sau `TOAST_DURATION_MS`;
 * giảm chuyển động thì gỡ ngay, không chờ animation.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [queue, dispatch] = useReducer(toastReducer, EMPTY_TOAST_QUEUE);
  const reduced = useReducedMotion();
  // Một hẹn giờ cho mỗi (thông báo, giai đoạn): "in" = đang hiện, chờ tự rời; "out" = đang rời, chờ gỡ.
  const timers = useRef(new Map<string, number>());

  useEffect(() => {
    const live = new Set<string>();
    for (const item of queue.items) {
      const key = `${item.id}:${item.leaving ? "out" : "in"}`;
      live.add(key);
      if (timers.current.has(key)) continue;
      const id = item.id;
      const timer = item.leaving
        ? window.setTimeout(() => dispatch({ type: "remove", id }), reduced ? 0 : LEAVE_FALLBACK_MS)
        : window.setTimeout(() => dispatch({ type: "leave", id }), TOAST_DURATION_MS);
      timers.current.set(key, timer);
    }
    for (const [key, timer] of timers.current) {
      if (live.has(key)) continue;
      window.clearTimeout(timer);
      timers.current.delete(key);
    }
  }, [queue.items, reduced]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) window.clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const show = useCallback((message: string) => dispatch({ type: "push", message }), []);
  const api = useMemo<ToastApi>(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className={styles.region} aria-live="polite">
        {queue.items.map((item) => (
          <Toast
            key={item.id}
            message={item.message}
            leaving={item.leaving}
            onClose={() => dispatch({ type: "leave", id: item.id })}
            onLeft={() => dispatch({ type: "remove", id: item.id })}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
