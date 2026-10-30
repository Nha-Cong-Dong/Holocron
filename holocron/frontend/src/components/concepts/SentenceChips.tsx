"use client";

import type { PassageSelection, SentenceRange } from "@/lib/passage";

import styles from "./ConceptsTab.module.css";

interface SentenceChipsProps {
  sentences: readonly SentenceRange[];
  selection: PassageSelection | null;
  /** Bấm một câu: chọn câu đó; bấm lại câu đang chọn: bỏ chọn. */
  onPick: (sentence: SentenceRange | null) => void;
}

/**
 * Chọn nhanh từng câu của đoạn (thay cho bôi chuột, dùng được bằng bàn phím). Mỗi chip là `<button aria-pressed>`;
 * nhãn cắt ngắn chỉ để hiển thị, câu đủ nằm ở `title` và giá trị gửi đi luôn là lát nguyên văn đầy đủ.
 */
export function SentenceChips({ sentences, selection, onPick }: SentenceChipsProps) {
  if (sentences.length === 0) return null;
  return (
    <fieldset className={styles.sentences} aria-label="Chọn nhanh một câu">
      {sentences.map((sentence) => {
        const pressed = selection !== null && selection.start === sentence.start && selection.end === sentence.end;
        return (
          <button
            key={`${sentence.start}-${sentence.end}`}
            type="button"
            className={styles.sentence}
            aria-pressed={pressed}
            title={sentence.text}
            aria-label={sentence.text}
            onClick={() => onPick(pressed ? null : sentence)}
          >
            {sentence.label}
          </button>
        );
      })}
    </fieldset>
  );
}
