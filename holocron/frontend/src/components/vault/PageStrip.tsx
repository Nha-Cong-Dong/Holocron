import { PAGE_STATUS_LABEL_VI } from "@/lib/documents";
import type { PageSummaryView } from "@/lib/documents-data";

import styles from "./Vault.module.css";

/**
 * Dải tiến độ đọc: mỗi ô là một trang của `DocumentImport.pages`, tô theo trạng thái backend trả (đang đọc để trống).
 * Không đếm gì ở giao diện; số trang chưa đọc hiện bằng chữ ngay trên dải, lấy từ `DocumentSummary.pages_pending`.
 */
export function PageStrip({ pages }: { pages: readonly PageSummaryView[] }) {
  return (
    <div
      className={styles.strip}
      role="img"
      aria-label="Dải trạng thái từng trang: ô trống là trang đang chờ đọc, ô tô màu là trang đã đọc xong."
    >
      {pages.map((page) => (
        <span
          key={page.pageNumber}
          className={styles.stripCell}
          data-status={page.status}
          title={`Trang ${page.pageNumber}: ${PAGE_STATUS_LABEL_VI[page.status]}`}
        />
      ))}
    </div>
  );
}
