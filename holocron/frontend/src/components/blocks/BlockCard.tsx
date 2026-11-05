import { withContentKeys } from "@/lib/keys";
import { blockTypeLabel, highlightParts, isUncertain, showsCrop } from "@/lib/search-results";
import type { Block, MatchSpan } from "@/types/holocron";

import styles from "./Blocks.module.css";
import { CropImage } from "./CropImage";

interface BlockCardProps {
  block: Block;
  /** Khoảng khớp do backend trả (kết quả tra cứu); rỗng khi xem khối của mục. */
  spans?: readonly MatchSpan[];
  /** Dòng ngữ cảnh phía trên (tài liệu, đường dẫn Section). */
  context?: string;
}

function TableBlock({ block }: { block: Block }) {
  const rows = block.cells;
  if (rows === null) return <p className={styles.text}>{block.text ?? ""}</p>;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <tbody>
          {withContentKeys(rows, (row) => row.map((cell) => `${cell.row}.${cell.col}`).join("|")).map(
            ({ key, item: row }) => (
              <tr key={key}>
                {row.map((cell) => (
                  <td key={`${cell.row}.${cell.col}`} rowSpan={cell.rowspan} colSpan={cell.colspan}>
                    {cell.text}
                  </td>
                ))}
              </tr>
            ),
          )}
        </tbody>
      </table>
    </div>
  );
}

function Content({ block, spans }: { block: Block; spans: readonly MatchSpan[] }) {
  if (block.type === "table") return <TableBlock block={block} />;
  if (block.type === "code") return <pre className={styles.code}>{block.text ?? ""}</pre>;
  const text = block.text ?? "";
  if (text === "") return null;
  return (
    <p className={block.type === "heading" ? styles.heading : styles.text}>
      {withContentKeys(highlightParts(text, spans), (part) => `${part.match}:${part.text}`).map(({ key, item }) =>
        item.match ? (
          <mark key={key} className={styles.mark}>
            {item.text}
          </mark>
        ) : (
          <span key={key}>{item.text}</span>
        ),
      )}
    </p>
  );
}

/**
 * Một khối: chữ bóc tách (hoặc bảng, code giữ khoảng trắng) CẠNH ảnh cắt trang gốc khi có (hình, công thức, khối có
 * cờ chưa chắc). Mọi nội dung do backend trả; chỗ này không tính hay suy ra số nào.
 */
export function BlockCard({ block, spans = [], context }: BlockCardProps) {
  const crop = showsCrop(block) && block.crop_ref !== null ? block.crop_ref : null;
  return (
    <article className={styles.card}>
      <header className={styles.meta}>
        {context !== undefined && <span className={styles.context}>{context}</span>}
        <span className={styles.badge}>{blockTypeLabel(block.type)}</span>
        <span>Trang {block.page_number}</span>
        {isUncertain(block) && <span className={styles.uncertain}>chưa chắc — xem ảnh gốc</span>}
      </header>
      <div className={crop === null ? styles.bodySingle : styles.body}>
        <div className={styles.content}>
          <Content block={block} spans={spans} />
        </div>
        {crop !== null && (
          <div className={styles.cropBox}>
            <CropImage
              documentId={block.document_id}
              cropRef={crop}
              alt={`Ảnh cắt trang ${block.page_number} của tài liệu gốc`}
            />
          </div>
        )}
      </div>
    </article>
  );
}
