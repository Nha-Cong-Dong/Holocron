"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { EXAM_UPLOAD_MAX_BYTES, EXAM_UPLOAD_MAX_MIB, QUANTUM_MAX, QUANTUM_MIN } from "@/lib/limits";

import styles from "./UploadForm.module.css";

const ACCEPTED_TYPES = "image/png,image/jpeg,image/webp,application/pdf";

const TOPICS: { key: string; label: string }[] = [
  { key: "os.cpu_sched.rr.avg_waiting_time", label: "Round Robin — thời gian chờ trung bình" },
];

export function UploadForm() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [topicKey, setTopicKey] = useState<string>(TOPICS[0]?.key ?? "");
  const [quantum, setQuantum] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (file === null) {
      setError("Hãy chọn ảnh hoặc PDF đề thi.");
      return;
    }
    if (file.size > EXAM_UPLOAD_MAX_BYTES) {
      setError(`File vượt quá ${EXAM_UPLOAD_MAX_MIB} MiB.`);
      return;
    }
    const trimmed = quantum.trim();
    const parsedQuantum = trimmed === "" ? null : Number(trimmed);
    if (
      parsedQuantum !== null &&
      (!Number.isInteger(parsedQuantum) || parsedQuantum < QUANTUM_MIN || parsedQuantum > QUANTUM_MAX)
    ) {
      setError(`Quantum phải là số nguyên từ ${QUANTUM_MIN} đến ${QUANTUM_MAX}, hoặc để trống để lấy từ đề.`);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const { item } = await api.uploadDocument(file, topicKey, parsedQuantum);
      router.push(`/ingestion?item=${encodeURIComponent(item.item_id)}`);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Tải lên thất bại.");
      setSubmitting(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={(event) => void handleSubmit(event)}>
      <label className={styles.field}>
        <span>Ảnh hoặc PDF đề thi</span>
        <input
          type="file"
          accept={ACCEPTED_TYPES}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          disabled={submitting}
          required
        />
      </label>
      <label className={styles.field}>
        <span>Dạng bài</span>
        <select value={topicKey} onChange={(event) => setTopicKey(event.target.value)} disabled={submitting}>
          {TOPICS.map((topic) => (
            <option key={topic.key} value={topic.key}>
              {topic.label}
            </option>
          ))}
        </select>
      </label>
      <label className={styles.field}>
        <span>Quantum (ms) — để trống nếu đề đã ghi</span>
        <input
          type="number"
          inputMode="numeric"
          min={QUANTUM_MIN}
          max={QUANTUM_MAX}
          value={quantum}
          onChange={(event) => setQuantum(event.target.value)}
          disabled={submitting}
        />
      </label>
      {error !== null && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <button type="submit" className={styles.submit} disabled={submitting || file === null}>
        {submitting ? "Đang quét dual-pass…" : "Tải lên & quét Ingestion Gate"}
      </button>
    </form>
  );
}
