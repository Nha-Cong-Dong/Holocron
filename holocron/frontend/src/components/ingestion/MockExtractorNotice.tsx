"use client";

import { useShell } from "@/components/shell/ShellContext";

import styles from "./IngestionView.module.css";

/**
 * Nhãn ở trang tải đề và trang item khi `RuntimeInfo.mock_extractor = true` (§5.12, field đề xuất §17 D8): bảng và
 * phương án là dữ liệu mẫu, không đọc từ file vừa tải. Backend chưa trả field này thì không hiện gì.
 */
export function MockExtractorNotice() {
  const { runtime } = useShell();
  if (runtime.runtime?.mock_extractor !== true) return null;
  return (
    <p className={styles.mockNotice}>
      <strong>Dữ liệu mẫu:</strong> backend chưa cấu hình mô hình đọc ảnh, nên bảng và phương án hiển thị là dữ liệu
      mẫu, không đọc từ file vừa tải.
    </p>
  );
}
