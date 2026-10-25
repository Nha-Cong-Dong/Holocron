import styles from "./OfflineBanner.module.css";

/**
 * Băng thông báo trên mọi trang khi backend báo `llm_configured = false` (§5.12; không dựa vào `demo_mode`, xem
 * `selectOfflineMode`). Câu chữ theo đề xuất §17 D10(a):
 * "chạy offline, không có LLM" thay cho "chế độ demo".
 */
export function OfflineBanner() {
  return (
    <aside className={styles.banner} aria-label="Chế độ chạy">
      <strong>Đang chạy offline, không có LLM.</strong> Nhãn và cặp khái niệm cần nhập tay; câu tính toán chỉ dùng
      template đã kiểm chứng.
    </aside>
  );
}
