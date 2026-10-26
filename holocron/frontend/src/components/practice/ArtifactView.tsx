import { GanttChart } from "@/components/twins/GanttChart";
import { withContentKeys } from "@/lib/keys";
import type { PracticeArtifact } from "@/lib/practice-data";

import styles from "./Practice.module.css";

/**
 * Artifact lời giải chọn theo `kind` (§5.11): `timeline` vẽ bằng sơ đồ thời gian có sẵn, `table` là bảng. Mọi giá trị
 * do backend (sandbox) trả; component chỉ vẽ lại, không tính gì (I1) và không rẽ nhánh theo topic.
 */
export function ArtifactView({ artifact }: { artifact: PracticeArtifact }) {
  if (artifact.kind === "timeline") {
    return (
      <div>
        {artifact.caption !== null && <p className={styles.muted}>{artifact.caption}</p>}
        <GanttChart
          segments={artifact.segments.map((segment) => ({
            process: segment.label,
            start: segment.start,
            end: segment.end,
          }))}
        />
      </div>
    );
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        {artifact.caption !== null && <caption>{artifact.caption}</caption>}
        <thead>
          <tr>
            {withContentKeys(artifact.headers, (header) => header).map(({ key, item }) => (
              <th key={key} scope="col">
                {item}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {withContentKeys(artifact.rows, (row) => JSON.stringify(row)).map(({ key, item }) => (
            <tr key={key}>
              {withContentKeys(item, (cell) => cell).map((cell) => (
                <td key={cell.key}>{cell.item}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
