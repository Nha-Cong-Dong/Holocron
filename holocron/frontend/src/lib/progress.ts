/**
 * Logic thuần của thanh tiến độ `Progress`. Số `value`/`max` do backend trả (hoặc là số đếm phần tử đã có trên màn);
 * module này KHÔNG sinh số mới để hiển thị (CLAUDE.md §2.1): chỉ kẹp tỉ lệ dùng cho bố cục (độ dài thanh, vị trí sao)
 * và ghép chữ "x/y <đơn vị>" từ chính hai số được truyền vào.
 */

/** Tỉ lệ bố cục trong `[0, 1]`; `max` không dương hoặc số không hữu hạn ⇒ 0. */
export function progressRatio(value: number, max: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(max) || max <= 0) return 0;
  return Math.min(Math.max(value / max, 0), 1);
}

/** Giá trị cho `aria-valuemax`: chính `max` khi là số dương hữu hạn, nếu không thì 0. */
export function progressAriaMax(max: number): number {
  return Number.isFinite(max) && max > 0 ? max : 0;
}

/** Giá trị cho `aria-valuenow`: chính `value` khi nằm trong `[0, max]`, nếu không thì là biên gần nhất. */
export function progressAriaNow(value: number, max: number): number {
  const top = progressAriaMax(max);
  if (!Number.isFinite(value) || value < 0) return 0;
  return Math.min(value, top);
}

function formatCount(count: number): string {
  return Number.isFinite(count) ? String(count) : "?";
}

/** Chữ cho `aria-valuetext` và nhãn hiển thị: "12/40 trang". Đơn vị rỗng ⇒ chỉ "12/40". */
export function progressValueText(value: number, max: number, unit: string): string {
  const base = `${formatCount(value)}/${formatCount(max)}`;
  const trimmed = unit.trim();
  return trimmed === "" ? base : `${base} ${trimmed}`;
}
