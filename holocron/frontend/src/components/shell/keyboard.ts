import type { TargetDescriptor } from "@/lib/shortcuts";

/** Mô tả phần tử đang giữ focus cho `targetKindOf` (logic thuần ở `lib/shortcuts.ts`); chỉ gọi trong handler phím. */
export function describeTarget(target: EventTarget | null): TargetDescriptor | null {
  if (!(target instanceof HTMLElement)) return null;
  return {
    tagName: target.tagName,
    isContentEditable: target.isContentEditable,
    inputType: target instanceof HTMLInputElement ? target.type : null,
  };
}
