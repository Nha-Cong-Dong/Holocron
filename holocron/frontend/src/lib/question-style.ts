import { freshRuntime, type RuntimeState } from "./runtime";

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
