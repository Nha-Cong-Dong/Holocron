"use client";
// Ba helper motion nhỏ, không phụ thuộc thư viện.
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { resolveReducedMotion } from "@/lib/motion-preference";

/**
 * true khi cần giảm chuyển động, khớp motion.css (`lib/motion-preference.ts`): `<html data-motion="reduce">` ⇒ giảm;
 * `data-motion="full"` ⇒ đầy đủ, thắng cài đặt của Windows; không có thuộc tính ⇒ theo Windows.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const read = () => setReduced(resolveReducedMotion(document.documentElement.dataset.motion, mq.matches));
    read();
    mq.addEventListener("change", read);
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-motion"] });
    return () => {
      mq.removeEventListener("change", read);
      obs.disconnect();
    };
  }, []);
  return reduced;
}

/** Đếm số lên tới target trong tối đa `ms` (mặc định 600). Giảm chuyển động: hiện số cuối ngay. */
export function useCountUp(target: number, ms = 600): number {
  const reduced = useReducedMotion();
  const [value, setValue] = useState(reduced ? target : 0);
  useEffect(() => {
    if (reduced) {
      setValue(target);
      return;
    }
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      setValue(Math.round(target * (1 - (1 - t) ** 3)));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms, reduced]);
  return value;
}

/**
 * FLIP cho danh sách: các phần tử còn lại trượt về chỗ mới khi một dòng bị xoá / thêm.
 * Dùng: const ref = useFlip<HTMLUListElement>(); <ul ref={ref}> mỗi <li data-flip-key={id}>
 * Đo lại sau mỗi lần render (không nhận mảng phụ thuộc) để không bỏ sót lần đổi thứ tự nào.
 */
export function useFlip<T extends HTMLElement>(duration = 200) {
  const ref = useRef<T>(null);
  const last = useRef(new Map<string, DOMRect>());
  const reduced = useReducedMotion();
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const items = Array.from(root.querySelectorAll<HTMLElement>("[data-flip-key]"));
    const next = new Map<string, DOMRect>();
    items.forEach((el) => {
      const key = el.dataset.flipKey;
      if (key === undefined) return;
      const box = el.getBoundingClientRect();
      next.set(key, box);
      const prev = last.current.get(key);
      if (!prev || reduced) return;
      const dx = prev.left - box.left;
      const dy = prev.top - box.top;
      if (!dx && !dy) return;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], {
        duration,
        easing: "cubic-bezier(.2,.8,.2,1)",
      });
    });
    last.current = next;
  });
  return ref;
}
