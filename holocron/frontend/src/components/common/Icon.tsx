// Bộ icon Holocron (lưới 24, nét 1,6; ở 16 px dùng strokeWidth 1.9). Màu theo currentColor.
// Icon trang trí: để mặc định aria-hidden. Icon mang nghĩa: truyền label.
const PATHS = {
  subject: ["M12 6.5c-2-1.6-4.6-2-8-1.5v13c3.4-.5 6 0 8 1.5 2-1.5 4.6-2 8-1.5V5c-3.4-.5-6-.1-8 1.5z", "M12 6.5v13"],
  document: ["M7 3.5h7l4 4v13H7z", "M14 3.5v4h4", "M10 12h5M10 15.5h3"],
  pdf: ["M7 3.5h7l4 4v13H7z", "M14 3.5v4h4", "M7 11.5h4a3 3 0 0 1 0 6H7a3 3 0 0 1 0-6z"],
  chapter: [
    "M5.8 15.6L9.2 9.4M11.4 8.9l3.3 2.2M16.6 10.5l1.8-3.9",
    "M3.4 17a1.6 1.6 0 1 0 3.2 0 1.6 1.6 0 1 0-3.2 0",
    "M8.4 8a1.6 1.6 0 1 0 3.2 0 1.6 1.6 0 1 0-3.2 0",
    "M14.4 12a1.6 1.6 0 1 0 3.2 0 1.6 1.6 0 1 0-3.2 0",
    "M17.4 5a1.6 1.6 0 1 0 3.2 0 1.6 1.6 0 1 0-3.2 0",
  ],
  concept: ["M12 3l2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2z"],
  practice: [
    "M3.5 12a8.5 8.5 0 1 0 17 0 8.5 8.5 0 1 0-17 0",
    "M7.5 12a4.5 4.5 0 1 0 9 0 4.5 4.5 0 1 0-9 0",
    "M11.2 12a.8.8 0 1 0 1.6 0 .8.8 0 1 0-1.6 0",
  ],
  quote: ["M8 5.5H5.5v13H8M16 5.5h2.5v13H16", "M9.5 10h5M9.5 14h3.5"],
  progress: ["M3.5 12a8.5 8.5 0 1 0 17 0 8.5 8.5 0 1 0-17 0", "M12 3.5a8.5 8.5 0 0 1 0 17z"],
  correct: ["M3.5 12a8.5 8.5 0 1 0 17 0 8.5 8.5 0 1 0-17 0", "M8.5 12.2l2.4 2.4 4.6-4.8"],
  wrong: ["M3.5 12a8.5 8.5 0 1 0 17 0 8.5 8.5 0 1 0-17 0", "M9.5 9.5l5 5M14.5 9.5l-5 5"],
  settings: [
    "M6 4v16M12 4v16M18 4v16",
    "M4 15a2 2 0 1 0 4 0 2 2 0 1 0-4 0",
    "M10 8a2 2 0 1 0 4 0 2 2 0 1 0-4 0",
    "M16 13a2 2 0 1 0 4 0 2 2 0 1 0-4 0",
  ],
  experiment: ["M3.5 13.5l11.5-5.8 2 4-11.5 5.8z", "M15 7.7l3-1.5 2 4-3 1.5", "M9.5 15.5L7 21M11 14.8l2.8 6.2"],
  warning: ["M12 4l9 15.5H3z", "M12 10v4M12 16.8v.2"],
  drop: ["M12 3.5v10M8 9.5l4 4 4-4", "M4 15.5v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"],
} satisfies Record<string, readonly string[]>;
// Chú ý: progress có phần nửa trái tô đặc (fill) — xem FILLED.
const FILLED: Partial<Record<IconName, readonly number[]>> = { progress: [1], practice: [2] };

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, label }: { name: IconName; size?: number; label?: string }) {
  const stroke = size <= 16 ? 1.9 : 1.6;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {PATHS[name].map((d, i) => (
        <path key={d} d={d} fill={FILLED[name]?.includes(i) ? "currentColor" : "none"} />
      ))}
    </svg>
  );
}
