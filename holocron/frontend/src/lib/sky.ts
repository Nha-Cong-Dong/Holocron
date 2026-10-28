/**
 * Bầu trời kiến thức (design-handoff/README.md mục 4): mỗi chương là một chòm sao, mỗi khái niệm đã nhận là một sao
 * vàng, khái niệm chờ duyệt là sao viền tím. Bố cục tất định (không ngẫu nhiên), toạ độ theo `buildSky()` của
 * `reference/Prototype.dc.html`. Module chỉ đếm cặp khái niệm backend trả theo `chapter_code` và `status`; số hiển
 * thị (số sao, số chờ duyệt) là số đếm đó, không sinh số mới (CLAUDE.md §2.1). Không rẽ nhánh theo môn hay chương.
 */

import type { ConceptPairStatus } from "@/types/holocron";

/** Kích thước hệ toạ độ của bầu trời (trang môn vẽ đúng 790×200; thẻ môn thu nhỏ cùng hệ toạ độ). */
export const SKY_WIDTH = 790;
export const SKY_HEIGHT = 200;
/** Số chòm vẽ tối đa trên trang môn; chương thừa gộp thành nhãn "+k chương". */
export const SKY_MAX_CONSTELLATIONS = 5;
/** Số chòm vẽ tối đa trên thẻ môn ở trang chủ. */
export const MINI_SKY_MAX_CONSTELLATIONS = 3;
/** Số sao vẽ được trong một chòm. */
export const STARS_PER_CONSTELLATION = 8;

/**
 * Ba hình chòm của prototype (`PTS`), mỗi hình 8 điểm `[x, y]`. Chòm thứ i dùng hình `i % 3`. Khung bao chung của
 * ba hình: x từ 15 tới 210, y từ 25 tới 125.
 */
const PATTERNS: readonly (readonly (readonly [number, number])[])[] = [
  [
    [20, 100],
    [55, 70],
    [95, 85],
    [120, 45],
    [160, 60],
    [185, 25],
    [150, 115],
    [200, 100],
  ],
  [
    [15, 40],
    [50, 25],
    [85, 55],
    [70, 100],
    [120, 90],
    [150, 120],
    [175, 70],
    [205, 40],
  ],
  [
    [20, 70],
    [60, 50],
    [90, 90],
    [130, 70],
    [150, 30],
    [190, 50],
    [170, 110],
    [210, 125],
  ],
];
const PATTERN_MIN_X = 15;
const PATTERN_MIN_Y = 25;
const PATTERN_WIDTH = 195;
const PATTERN_HEIGHT = 100;
/** Khoảng trống hai bên chòm trong ô của nó. */
const SLOT_GUTTER = 24;
/** Vùng vẽ sao theo chiều dọc: có nhãn chương thì chừa phần dưới cho nhãn. */
const AREA_WITH_LABELS = { top: 18, bottom: 140 } as const;
const AREA_WITHOUT_LABELS = { top: 20, bottom: 180 } as const;
/** Vị trí hai dòng nhãn dưới chòm (toạ độ y của mép trên dòng chữ). */
const LABEL_NAME_Y = 146;
const LABEL_DETAIL_Y = 166;

const CHAPTER_LABEL_PATTERN = /^ch(\d+)(?:\.(\d+))?$/;

/** Cặp khái niệm tối thiểu để dựng bầu trời (`ConceptPair` của backend thoả kiểu này). */
export interface SkyPair {
  chapter_code: string;
  status: ConceptPairStatus;
}

export interface SkyChapter {
  code: string;
  label: string;
  /** Số cặp `confirmed` của chương (số sao). */
  confirmed: number;
  /** Số cặp `proposed` của chương (chờ duyệt). */
  proposed: number;
}

export type SkyStarKind = "confirmed" | "proposed" | "empty";

export interface SkyStar {
  x: number;
  y: number;
  kind: SkyStarKind;
}

export interface SkyLine {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface SkyConstellation {
  chapter: SkyChapter;
  stars: SkyStar[];
  lines: SkyLine[];
  /** Tâm ngang và độ rộng ô của chòm (để đặt nhãn). */
  centerX: number;
  slotWidth: number;
  /** Dòng nhãn: tên chương và số thật. */
  name: string;
  detail: string;
  /** Có sao không vẽ được vì chòm đã đủ 8 chỗ. */
  overflow: boolean;
}

export interface SkyLayout {
  width: number;
  height: number;
  constellations: SkyConstellation[];
  /** Có vẽ nhãn chương dưới mỗi chòm hay không. */
  withLabels: boolean;
  labelNameY: number;
  labelDetailY: number;
  /** Số chương không vẽ được (ngoài số chòm tối đa); 0 khi vẽ đủ. */
  hiddenChapters: number;
  /** "+k chương" khi có chương ẩn, `null` khi không. */
  hiddenLabel: string | null;
  /** Câu gợi ý bước tiếp theo khi chưa có chương nào (chỉ bầu trời có nhãn, tức trang môn); `null` khi không cần. */
  emptyHint: string | null;
  /** Câu đọc cho trình đọc màn hình: số thật của mọi chương (kể cả chương ẩn, sao không vẽ được) và câu gợi ý nếu có. */
  ariaLabel: string;
}

export interface SkyOptions {
  maxConstellations: number;
  withLabels: boolean;
}

export const FULL_SKY: SkyOptions = { maxConstellations: SKY_MAX_CONSTELLATIONS, withLabels: true };
export const MINI_SKY: SkyOptions = { maxConstellations: MINI_SKY_MAX_CONSTELLATIONS, withLabels: false };

/** Gợi ý trên bầu trời trống của trang môn. */
export const EMPTY_SKY_HINT = "Chưa có sao nào. Nhận khái niệm ở tab Khái niệm để thắp sao đầu tiên.";

/**
 * So sánh mã chương theo thứ tự tự nhiên: phần số so theo giá trị (`ch2` < `ch10`, `ch2.9` < `ch2.10`), phần chữ so
 * theo mã ký tự (không phụ thuộc ngôn ngữ của máy). Bằng nhau về số thì mã ngắn hơn (ít số 0 đầu) đứng trước.
 */
export function compareChapterCodes(a: string, b: string): number {
  const left = a.split(/(\d+)/);
  const right = b.split(/(\d+)/);
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const x = left[index];
    const y = right[index];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    if (x === y) continue;
    if (index % 2 === 1) {
      const diff = Number(x) - Number(y);
      if (diff !== 0) return diff < 0 ? -1 : 1;
      return x.length < y.length ? -1 : 1;
    }
    return x < y ? -1 : 1;
  }
  return 0;
}

/** Nhãn chương: `ch2` ⇒ "Chương 2", `ch2.1` ⇒ "Chương 2.1"; mã khác mẫu giữ nguyên; mã rỗng ⇒ "Chưa rõ chương". */
export function chapterLabel(code: string): string {
  const match = CHAPTER_LABEL_PATTERN.exec(code);
  if (match === null) return code.trim() === "" ? "Chưa rõ chương" : code;
  const major = String(Number(match[1]));
  return match[2] === undefined ? `Chương ${major}` : `Chương ${major}.${String(Number(match[2]))}`;
}

/**
 * Gom cặp khái niệm theo `chapter_code`, đếm `confirmed` và `proposed` (cặp `rejected` không thành sao nhưng chương
 * vẫn có mặt), sắp xếp chương theo thứ tự tự nhiên.
 */
export function countChapters(pairs: readonly SkyPair[]): SkyChapter[] {
  const byCode = new Map<string, SkyChapter>();
  for (const pair of pairs) {
    const code = pair.chapter_code;
    const chapter = byCode.get(code) ?? { code, label: chapterLabel(code), confirmed: 0, proposed: 0 };
    if (pair.status === "confirmed") chapter.confirmed += 1;
    if (pair.status === "proposed") chapter.proposed += 1;
    byCode.set(code, chapter);
  }
  return [...byCode.values()].sort((a, b) => compareChapterCodes(a.code, b.code));
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function chapterDetail(chapter: SkyChapter, overflow: boolean): string {
  if (overflow) return `${chapter.confirmed} đã nhận · ${chapter.proposed} chờ duyệt`;
  const stars = `${chapter.confirmed} sao`;
  return chapter.proposed > 0 ? `${stars} · ${chapter.proposed} chờ duyệt` : stars;
}

function chapterSentence(chapter: SkyChapter): string {
  const stars = `${chapter.label} có ${chapter.confirmed} sao`;
  return chapter.proposed > 0 ? `${stars}, ${chapter.proposed} khái niệm chờ duyệt` : stars;
}

/** Câu `aria-label` của bầu trời môn `title`: đủ số thật của mọi chương. */
export function skyAriaLabel(title: string, chapters: readonly SkyChapter[]): string {
  if (chapters.length === 0) return `Bầu trời môn ${title}: chưa có khái niệm nào.`;
  return `Bầu trời môn ${title}, ${chapters.length} chương: ${chapters.map(chapterSentence).join("; ")}.`;
}

function buildConstellation(
  chapter: SkyChapter,
  index: number,
  slotWidth: number,
  area: { top: number; bottom: number },
): SkyConstellation {
  const pattern = PATTERNS[index % PATTERNS.length] ?? [];
  const areaHeight = area.bottom - area.top;
  const scale = Math.min(1, (slotWidth - SLOT_GUTTER) / PATTERN_WIDTH, areaHeight / PATTERN_HEIGHT);
  const originX = index * slotWidth + (slotWidth - PATTERN_WIDTH * scale) / 2;
  const originY = area.top + (areaHeight - PATTERN_HEIGHT * scale) / 2;
  const lit = Math.min(STARS_PER_CONSTELLATION, chapter.confirmed);
  const pending = Math.min(STARS_PER_CONSTELLATION - lit, chapter.proposed);
  const points = pattern.map(([px, py]) => ({
    x: round1(originX + (px - PATTERN_MIN_X) * scale),
    y: round1(originY + (py - PATTERN_MIN_Y) * scale),
  }));
  const stars: SkyStar[] = points.map((point, slot) => ({
    ...point,
    kind: slot < lit ? "confirmed" : slot < lit + pending ? "proposed" : "empty",
  }));
  const lines: SkyLine[] = [];
  for (let slot = 0; slot + 1 < lit; slot += 1) {
    const from = points[slot];
    const to = points[slot + 1];
    if (from === undefined || to === undefined) continue;
    lines.push({ x1: from.x, y1: from.y, x2: to.x, y2: to.y });
  }
  const overflow = chapter.confirmed + chapter.proposed > STARS_PER_CONSTELLATION;
  return {
    chapter,
    stars,
    lines,
    centerX: round1(index * slotWidth + slotWidth / 2),
    slotWidth: round1(slotWidth),
    name: chapter.label,
    detail: chapterDetail(chapter, overflow),
    overflow,
  };
}

/**
 * Bố cục bầu trời của môn `title` từ các cặp khái niệm: tối đa `maxConstellations` chòm chia đều chiều ngang (chương
 * thừa ⇒ nhãn "+k chương"), tối đa 8 sao mỗi chòm (vàng trước, tím lấp chỗ còn lại, còn lại là sao mờ), nét nối giữa
 * các sao vàng liên tiếp như prototype.
 */
export function buildSkyLayout(title: string, pairs: readonly SkyPair[], options: SkyOptions = FULL_SKY): SkyLayout {
  const chapters = countChapters(pairs);
  const max = Math.max(1, Math.floor(options.maxConstellations));
  const shown = chapters.slice(0, max);
  const hiddenChapters = chapters.length - shown.length;
  const slotWidth = shown.length === 0 ? SKY_WIDTH : SKY_WIDTH / shown.length;
  const area = options.withLabels ? AREA_WITH_LABELS : AREA_WITHOUT_LABELS;
  const emptyHint = options.withLabels && chapters.length === 0 ? EMPTY_SKY_HINT : null;
  const counts = skyAriaLabel(title, chapters);
  return {
    width: SKY_WIDTH,
    height: SKY_HEIGHT,
    constellations: shown.map((chapter, index) => buildConstellation(chapter, index, slotWidth, area)),
    withLabels: options.withLabels,
    labelNameY: LABEL_NAME_Y,
    labelDetailY: LABEL_DETAIL_Y,
    hiddenChapters,
    hiddenLabel: hiddenChapters > 0 ? `+${hiddenChapters} chương` : null,
    emptyHint,
    ariaLabel: emptyHint === null ? counts : `${counts} ${emptyHint}`,
  };
}
