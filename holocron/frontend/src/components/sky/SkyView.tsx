"use client";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { Button } from "@/components/ui";
import type { SkyLayout } from "@/lib/sky";

import styles from "./SkyView.module.css";

/** Bán kính sao (toạ độ của bầu trời 790×200): sao vàng, sao chờ duyệt, sao mờ. */
const STAR_RADIUS = { confirmed: 5, proposed: 4.5, empty: 2.5 } as const;

export type SkyViewProps = {
  /** `full`: đầu trang môn (790×200, có nhãn chương). `mini`: thẻ môn ở trang chủ. */
  size: "full" | "mini";
  className?: string;
} & (
  | {
      status: "ready";
      layout: SkyLayout;
      /** Lần tải lại gần nhất lỗi: vẫn vẽ bầu trời cũ, kèm một dòng nhỏ "Chưa cập nhật được". */
      stale?: boolean;
    }
  | { status: "loading" }
  | { status: "error"; onRetry: () => void; retrying?: boolean }
);

function joinClasses(...names: readonly (string | undefined)[]): string {
  return names.filter((name) => name !== undefined && name !== "").join(" ");
}

/**
 * Bầu trời kiến thức (design-handoff/README.md mục 4): ô luôn tối ở cả hai theme; mỗi chương một chòm sao, sao vàng là
 * khái niệm đã nhận, sao viền tím nhấp nháy là khái niệm chờ duyệt. `role="img"` với `aria-label` đọc đủ số thật mọi
 * chương và câu gợi ý khi trống (do `lib/sky.ts` dựng). Đang tải và lỗi vẽ trong cùng ô tối để không đẩy bố cục.
 */
export function SkyView(props: SkyViewProps) {
  const frame = joinClasses(styles.frame, props.size === "mini" ? styles.mini : styles.full, props.className);

  if (props.status === "loading") {
    return (
      <div className={frame} aria-busy="true">
        <div className={styles.message} role="status">
          <HolocronMark size={22} spinning />
          <span>Đang tải bầu trời…</span>
        </div>
      </div>
    );
  }

  if (props.status === "error") {
    return (
      <div className={frame}>
        <div className={styles.message}>
          <span>Chưa tải được bầu trời của môn này.</span>
          <Button variant="secondary" busy={props.retrying} busyLabel="Đang thử lại" onClick={props.onRetry}>
            Thử lại
          </Button>
        </div>
      </div>
    );
  }

  const { layout } = props;
  return (
    <div className={frame}>
      <div className={styles.sky} role="img" aria-label={layout.ariaLabel}>
        <svg
          className={styles.canvas}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          preserveAspectRatio="xMidYMid meet"
          aria-hidden="true"
          focusable="false"
        >
          {layout.constellations.map((constellation) => (
            <g key={constellation.chapter.code}>
              {constellation.lines.map((line) => (
                <line
                  key={`${line.x1}-${line.y1}-${line.x2}-${line.y2}`}
                  className={styles.line}
                  x1={line.x1}
                  y1={line.y1}
                  x2={line.x2}
                  y2={line.y2}
                />
              ))}
              {constellation.stars.map((star) => (
                <circle
                  key={`${star.x}-${star.y}`}
                  className={joinClasses(styles[star.kind], star.kind === "proposed" ? "m-star-pending" : undefined)}
                  cx={star.x}
                  cy={star.y}
                  r={STAR_RADIUS[star.kind]}
                />
              ))}
            </g>
          ))}
        </svg>
        {layout.withLabels &&
          layout.constellations.map((constellation) => (
            <span
              key={constellation.chapter.code}
              className={styles.label}
              style={{
                left: `${((constellation.centerX - constellation.slotWidth / 2) / layout.width) * 100}%`,
                width: `${(constellation.slotWidth / layout.width) * 100}%`,
                top: `${(layout.labelNameY / layout.height) * 100}%`,
              }}
              aria-hidden="true"
            >
              <b className={styles.labelName}>{constellation.name}</b>
              <span className={styles.labelDetail}>{constellation.detail}</span>
            </span>
          ))}
        {layout.hiddenLabel !== null && (
          <span className={styles.more} aria-hidden="true">
            {layout.hiddenLabel}
          </span>
        )}
        {layout.emptyHint !== null && (
          <p className={styles.emptyText} aria-hidden="true">
            {layout.emptyHint}
          </p>
        )}
      </div>
      {props.stale === true && <output className={styles.stale}>Chưa cập nhật được</output>}
    </div>
  );
}
