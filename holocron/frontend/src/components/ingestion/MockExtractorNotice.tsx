"use client";

import { useShell } from "@/components/shell/ShellContext";
import { freshRuntime, mockExtractorNotice } from "@/lib/runtime";

import styles from "./IngestionView.module.css";

/**
 * Nhãn ở trang tải đề và trang item khi bảng và phương án là dữ liệu mẫu, không đọc từ file vừa tải (§5.12, field
 * `mock_extractor` đề xuất §17 D8). Backend chưa trả field này mà `demo_mode` đang bật thì hiện nhãn kèm chữ "có thể",
 * vì với backend hiện tại `demo_mode` bám theo extractor mẫu; giả định này nghiêng về an toàn (báo thừa hơn báo thiếu).
 */
export function MockExtractorNotice() {
  const { runtime } = useShell();
  const notice = mockExtractorNotice(freshRuntime(runtime));
  if (notice === null) return null;
  return (
    <p className={styles.mockNotice}>
      <strong>Dữ liệu mẫu:</strong>{" "}
      {notice === "confirmed"
        ? "backend chưa cấu hình mô hình đọc ảnh, nên bảng và phương án hiển thị là dữ liệu mẫu, không đọc từ file vừa tải."
        : "backend chưa báo rõ nguồn dữ liệu nhưng đang chạy ở chế độ chưa cấu hình mô hình đọc ảnh, nên bảng và phương án có thể là dữ liệu mẫu, không đọc từ file vừa tải."}
    </p>
  );
}
