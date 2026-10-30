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
import { EMPTY_TOAST_QUEUE, toastDeadline, toastReducer } from "@/lib/toast-queue";

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

/** Đưa vùng thông báo lên đỉnh top layer (trên `<dialog>` modal mở sau nó). Trình duyệt chưa có popover thì bỏ qua. */
function raiseRegion(region: HTMLElement | null, force: boolean): void {
  if (region === null || typeof region.showPopover !== "function") return;
  const open = region.matches(":popover-open");
  if (open && !force) return;
  if (open) region.hidePopover();
  region.showPopover();
}

/**
 * Hàng đợi thông báo nhanh cho toàn app (hàng đợi thuần ở `lib/toast-queue.ts`). Vùng hiển thị luôn có trong DOM với
 * `aria-live="polite"` để trình đọc màn hình đọc thông báo mới, và là popover thủ công trong top layer để không bị
 * `<dialog>` modal che; khi đang có hộp thoại modal, vùng được đưa lên lại trước khi thêm thông báo. Mỗi thông báo tự
 * rời đi sau `TOAST_DURATION_MS`; rê chuột hoặc focus vào vùng thì tạm dừng đếm (WCAG 2.2.1). Giảm chuyển động thì gỡ
 * ngay, không chờ animation.
 *
 * Giới hạn: khi đang có `<dialog>` modal, trình duyệt coi mọi thứ ngoài hộp thoại (kể cả vùng thông báo trong top
 * layer) là inert: thông báo vẫn nhìn thấy nhưng không bấm được nút đóng, không focus được và trình đọc màn hình có thể
 * không đọc. Vì người dùng không tương tác được, lúc đó vùng không tạm dừng: thông báo vẫn tự ẩn.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [queue, dispatch] = useReducer(toastReducer, EMPTY_TOAST_QUEUE);
  const reduced = useReducedMotion();
  const regionRef = useRef<HTMLDivElement>(null);
  // Một hẹn giờ cho mỗi (thông báo, giai đoạn): "in:<lúc bắt đầu>" = đang hiện, chờ tự rời; "out" = đang rời, chờ gỡ.
  const timers = useRef(new Map<string, number>());
  // Con trỏ đang ở trong vùng / focus đang ở trong vùng: quyết định tạm dừng đếm giờ.
  const presence = useRef({ hovered: false, focused: false });

  const syncPause = useCallback(() => {
    const { hovered, focused } = presence.current;
    const modalOpen = document.querySelector("dialog:modal") !== null;
    dispatch({ type: (hovered || focused) && !modalOpen ? "pause" : "resume", now: Date.now() });
  }, []);

  useEffect(() => {
    raiseRegion(regionRef.current, false);
  }, []);

  // Tạm dừng khi rê chuột hoặc focus trong vùng thông báo (nghe trực tiếp trên vùng, không phải thao tác của vùng).
  useEffect(() => {
    const region = regionRef.current;
    if (region === null) return;
    const state = presence.current;
    function onPointerEnter(): void {
      state.hovered = true;
      syncPause();
    }
    function onPointerLeave(): void {
      state.hovered = false;
      syncPause();
    }
    function onFocusIn(): void {
      state.focused = true;
      syncPause();
    }
    function onFocusOut(event: FocusEvent): void {
      const next = event.relatedTarget;
      state.focused = next instanceof Node && region !== null && region.contains(next);
      syncPause();
    }
    region.addEventListener("pointerenter", onPointerEnter);
    region.addEventListener("pointerleave", onPointerLeave);
    region.addEventListener("focusin", onFocusIn);
    region.addEventListener("focusout", onFocusOut);
    return () => {
      region.removeEventListener("pointerenter", onPointerEnter);
      region.removeEventListener("pointerleave", onPointerLeave);
      region.removeEventListener("focusin", onFocusIn);
      region.removeEventListener("focusout", onFocusOut);
    };
  }, [syncPause]);

  // Thông báo đang giữ focus bị gỡ khỏi DOM thì không có `focusout`; vùng trống thì con trỏ không còn ở trên thông báo
  // nào. Đặt lại trạng thái để thông báo sau không bị tạm dừng mãi.
  useEffect(() => {
    const region = regionRef.current;
    const state = presence.current;
    let changed = false;
    if (state.focused && !(region?.contains(document.activeElement) ?? false)) {
      state.focused = false;
      changed = true;
    }
    if (state.hovered && queue.items.length === 0) {
      state.hovered = false;
      changed = true;
    }
    if (changed) syncPause();
  }, [queue.items, syncPause]);

  useEffect(() => {
    const live = new Set<string>();
    for (const item of queue.items) {
      const deadline = toastDeadline(item);
      if (!item.leaving && deadline === null) continue;
      const key = item.leaving ? `${item.id}:out` : `${item.id}:in:${item.startedAt}`;
      live.add(key);
      if (timers.current.has(key)) continue;
      const id = item.id;
      const timer =
        deadline === null
          ? window.setTimeout(() => dispatch({ type: "remove", id }), reduced ? 0 : LEAVE_FALLBACK_MS)
          : window.setTimeout(() => dispatch({ type: "leave", id }), Math.max(deadline - Date.now(), 0));
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

  const show = useCallback(
    (message: string) => {
      // Hộp thoại modal mở sau vùng thông báo nằm trên nó trong top layer: đưa vùng lên lại trước khi thêm chữ mới, và
      // bỏ tạm dừng (vùng đang inert nên người dùng không tương tác được).
      if (document.querySelector("dialog:modal") !== null) {
        raiseRegion(regionRef.current, true);
        syncPause();
      }
      dispatch({ type: "push", message, now: Date.now() });
    },
    [syncPause],
  );
  const api = useMemo<ToastApi>(() => ({ show }), [show]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div ref={regionRef} className={styles.region} popover="manual" aria-live="polite">
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
