/**
 * Sao vừa sáng thêm trên bầu trời của trang môn sau khi danh sách khái niệm đổi (nhận, tạo khái niệm). So hai bố cục
 * do `buildSkyLayout` dựng (giới hạn 8 sao mỗi chòm, 5 chòm mỗi bầu trời giữ nguyên), theo mã chương: chỉ đếm sao vàng
 * thực sự vẽ ra. Số này dùng để gắn animation `m-star-new` và câu "N ngôi sao sáng lên"; không phải số kiến thức.
 */

import type { SkyConstellation, SkyLayout } from "@/lib/sky";

export interface SkyGains {
  /** Mã chương ⇒ số sao vàng vẽ thêm so với bố cục trước (chỉ chương có tăng). */
  byChapter: ReadonlyMap<string, number>;
  /** Tổng số sao vàng vẽ thêm. */
  total: number;
}

export const NO_SKY_GAINS: SkyGains = { byChapter: new Map(), total: 0 };

function litStars(constellation: SkyConstellation): number {
  return constellation.stars.filter((star) => star.kind === "confirmed").length;
}

/**
 * Sao vàng vẽ thêm từ bố cục `previous` sang `next`. Lần vẽ đầu (`previous = null`) không tính là sáng thêm. Chương
 * mới hiện trên bầu trời: mọi sao vàng của nó là mới. Chòm đã đủ 8 sao, chương không còn hiện, hay số sao giảm: 0.
 */
export function skyGains(previous: SkyLayout | null, next: SkyLayout): SkyGains {
  if (previous === null) return NO_SKY_GAINS;
  const before = new Map(previous.constellations.map((item) => [item.chapter.code, litStars(item)]));
  const byChapter = new Map<string, number>();
  let total = 0;
  for (const constellation of next.constellations) {
    const gain = litStars(constellation) - (before.get(constellation.chapter.code) ?? 0);
    if (gain <= 0) continue;
    byChapter.set(constellation.chapter.code, gain);
    total += gain;
  }
  return { byChapter, total };
}

/** Cờ "sao mới" cho từng sao của chòm: `gain` sao vàng cuối cùng (sao vàng luôn đứng trước) là sao mới. */
export function newStarFlags(constellation: SkyConstellation, gain: number): boolean[] {
  const lit = litStars(constellation);
  const firstNew = lit - Math.max(0, Math.min(gain, lit));
  return constellation.stars.map((star, index) => star.kind === "confirmed" && index >= firstNew && index < lit);
}
