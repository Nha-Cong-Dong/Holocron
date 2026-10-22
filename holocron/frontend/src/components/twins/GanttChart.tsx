import { GANTT_PALETTE_SIZE, ganttDescription, ganttLayout, tickPercent } from "@/lib/twin";
import type { GanttSegment } from "@/types/holocron";

import styles from "./GanttChart.module.css";

interface GanttChartProps {
  segments: readonly GanttSegment[];
}

/**
 * Sơ đồ Gantt chỉ vẽ lại các đoạn do sandbox trả (I1); không tính gì thêm. Phần hình ảnh ẩn khỏi trình đọc màn hình,
 * văn bản thay thế liệt kê từng đoạn trong `figcaption`.
 */
export function GanttChart({ segments }: GanttChartProps) {
  if (segments.length === 0) {
    return <p className={styles.empty}>Không có đoạn Gantt.</p>;
  }
  const layout = ganttLayout(segments);
  return (
    <figure className={styles.figure}>
      <div className={styles.visual} aria-hidden="true">
        <div className={styles.lane}>
          {layout.bars.map((bar) => (
            <div
              key={`${bar.process}-${bar.start}`}
              className={styles.bar}
              style={{
                left: `${bar.leftPercent}%`,
                width: `${bar.widthPercent}%`,
                background: `var(--gantt-${bar.colorIndex + 1})`,
              }}
              title={`${bar.process}: ${bar.start}–${bar.end}`}
            >
              <span className={styles.barLabel}>{bar.process}</span>
            </div>
          ))}
        </div>
        <div className={styles.axis}>
          {layout.ticks.map((tick) => (
            <span key={tick} className={styles.tick} style={{ left: `${tickPercent(tick, layout.axisEnd)}%` }}>
              {tick}
            </span>
          ))}
        </div>
        <ul className={styles.legend}>
          {layout.processes.map((process, index) => (
            <li key={process} className={styles.legendItem}>
              <span
                className={styles.swatch}
                style={{ background: `var(--gantt-${(index % GANTT_PALETTE_SIZE) + 1})` }}
              />
              {process}
            </li>
          ))}
        </ul>
      </div>
      <figcaption className={styles.caption}>
        <span>Sơ đồ Gantt</span>
        <span className={styles.srOnly}> — {ganttDescription(segments)}</span>
      </figcaption>
    </figure>
  );
}
