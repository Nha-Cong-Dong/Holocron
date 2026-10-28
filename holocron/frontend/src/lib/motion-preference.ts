/**
 * Quyết định "có giảm chuyển động không" cho code JS, khớp đúng hai khối giảm chuyển động của `app/motion.css`:
 * - `<html data-motion="reduce">` (công tắc trong Cài đặt) ⇒ giảm, bất kể hệ điều hành;
 * - `<html data-motion="full">` ⇒ đầy đủ chuyển động, thắng `prefers-reduced-motion` (khối CSS dùng
 *   `:root:not([data-motion="full"])`);
 * - không có thuộc tính (hoặc giá trị lạ) ⇒ theo hệ điều hành.
 */

export const MOTION_ATTRIBUTE_REDUCE = "reduce";
export const MOTION_ATTRIBUTE_FULL = "full";

export function resolveReducedMotion(
  motionAttribute: string | null | undefined,
  systemPrefersReduce: boolean,
): boolean {
  if (motionAttribute === MOTION_ATTRIBUTE_REDUCE) return true;
  if (motionAttribute === MOTION_ATTRIBUTE_FULL) return false;
  return systemPrefersReduce;
}
