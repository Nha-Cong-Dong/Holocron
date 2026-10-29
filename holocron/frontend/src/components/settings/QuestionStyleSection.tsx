"use client";

import { useShell } from "@/components/shell/ShellContext";
import { freshRuntime, type RuntimeState } from "@/lib/runtime";

import { LanguageModelTechnical } from "./CapabilityTechnical";
import { TechnicalDetails } from "./ConnectionTechnical";
import styles from "./SettingsView.module.css";

/** Chữ trạng thái của mục "Câu hỏi tự nhiên hơn" và cờ "đang bật" (tô màu). */
export interface QuestionStyleStatus {
  text: string;
  on: boolean;
}

/**
 * Trạng thái mô hình ngôn ngữ theo lần kiểm tra backend: đang kiểm tra ⇒ "Đang kiểm tra…" (không báo "Chưa kiểm tra
 * được" khi lần kiểm tra chưa xong); kiểm tra lỗi ⇒ "Chưa kiểm tra được"; kiểm tra xong ⇒ bật hay chưa bật.
 */
export function questionStyleStatus(state: RuntimeState): QuestionStyleStatus {
  if (state.backend === "connecting") return { text: "Đang kiểm tra…", on: false };
  const current = freshRuntime(state);
  if (current === null) return { text: "Chưa kiểm tra được. Bấm “Kiểm tra lại” ở mục Máy của bạn.", on: false };
  return current.llm_configured
    ? { text: "Đang bật", on: true }
    : { text: "Chưa bật. Tắt đi, app vẫn ra đề bình thường.", on: false };
}

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
      <p className={styles.state} data-on={on} aria-busy={runtime.backend === "connecting" || undefined}>
        {status}
      </p>
      <TechnicalDetails>
        <LanguageModelTechnical runtime={current} />
      </TechnicalDetails>
    </section>
  );
}
