"use client";

import { useShell } from "@/components/shell/ShellContext";
import { questionStyleStatus } from "@/lib/question-style";
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
  const { text: status, on } = questionStyleStatus(runtime);

  return (
    <section className={styles.panel} aria-labelledby="settings-question-style">
      <div>
        <h2 id="settings-question-style" className={styles.heading}>
          Câu hỏi tự nhiên hơn (tuỳ chọn)
        </h2>
        <p className={styles.caption}>Dùng mô hình ngôn ngữ để viết câu hỏi đa dạng hơn.</p>
      </div>
      <output className={styles.state} data-on={on} aria-busy={runtime.backend === "connecting" || undefined}>
        {status}
      </output>
      <TechnicalDetails>
        <LanguageModelTechnical runtime={current} />
      </TechnicalDetails>
    </section>
  );
}
