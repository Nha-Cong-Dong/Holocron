"use client";

import { useShell } from "@/components/shell/ShellContext";
import { freshRuntime } from "@/lib/runtime";

import { LanguageModelTechnical } from "./CapabilityTechnical";
import { TechnicalDetails } from "./ConnectionTechnical";
import styles from "./SettingsView.module.css";

/**
 * Cài đặt › "Câu hỏi tự nhiên hơn (tuỳ chọn)" (design-handoff/README.md màn 11): CHỈ ĐỌC trạng thái mô hình ngôn ngữ
 * backend báo (`llm_configured`), không có công tắc vì giao diện không bật được. Cách bật nằm trong "Chi tiết kỹ
 * thuật".
 */
export function QuestionStyleSection() {
  const { runtime } = useShell();
  const current = freshRuntime(runtime);
  const on = current?.llm_configured === true;
  const status =
    current === null
      ? "Chưa kiểm tra được. Bấm “Kiểm tra lại” ở mục Máy của bạn."
      : on
        ? "Đang bật"
        : "Chưa bật. Tắt đi, app vẫn ra đề bình thường.";

  return (
    <section className={styles.panel} aria-labelledby="settings-question-style">
      <div>
        <h2 id="settings-question-style" className={styles.heading}>
          Câu hỏi tự nhiên hơn (tuỳ chọn)
        </h2>
        <p className={styles.caption}>Dùng mô hình ngôn ngữ để viết câu hỏi đa dạng hơn.</p>
      </div>
      <p className={styles.state} data-on={on}>
        {status}
      </p>
      <TechnicalDetails>
        <LanguageModelTechnical runtime={current} />
      </TechnicalDetails>
    </section>
  );
}
