"use client";

import { useSearchParams } from "next/navigation";

import { IngestionReview } from "./IngestionReview";
import styles from "./IngestionView.module.css";
import { MockExtractorNotice } from "./MockExtractorNotice";
import { UploadForm } from "./UploadForm";

/**
 * `/ingestion`: không có `?item=` thì hiện form tải đề, có thì hiện màn kiểm duyệt item đó. ID đi qua query string
 * để trang build được ở chế độ static export (kế hoạch UI mục 4.3). Phải nằm trong `<Suspense>`.
 */
export function IngestionView() {
  const itemId = useSearchParams().get("item")?.trim() ?? "";
  if (itemId === "") {
    return <UploadPanel />;
  }
  return <IngestionReview key={itemId} itemId={itemId} />;
}

function UploadPanel() {
  return (
    <div className={styles.page}>
      <p>
        Tải ảnh đề thi lên. Hệ thống đọc bảng hai lượt độc lập; ô nào hai lượt đọc lệch nhau hoặc độ tin cậy thấp sẽ
        được tô đỏ để bạn xác nhận trước khi hệ thống tính toán.
      </p>
      <MockExtractorNotice />
      <UploadForm />
    </div>
  );
}
