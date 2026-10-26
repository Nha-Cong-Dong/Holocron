"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { clampTwinCount, DEFAULT_TWIN_COUNT, MAX_TWIN_COUNT, MIN_TWIN_COUNT } from "@/lib/twin";
import type { TwinBatch } from "@/types/holocron";
import styles from "./TwinsView.module.css";
import { TwinViewer } from "./TwinViewer";

type BatchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; error: UiError }
  | { kind: "ready"; batch: TwinBatch };

/** Sinh và xem `TwinBatch` của một blueprint; mọi con số do backend trả. */
export function TwinBatchPanel({ blueprintId }: { blueprintId: string }) {
  const [count, setCount] = useState(DEFAULT_TWIN_COUNT);
  // Một khoá cho mỗi lần mở bộ đề: bấm lặp hoặc gửi lại cùng body trong 24 h nhận đúng lượt sinh cũ, không tính lại.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [state, setState] = useState<BatchState>({ kind: "idle" });

  function generate(): void {
    setState({ kind: "loading" });
    api
      .createTwinBatch(blueprintId, { count }, idempotencyKey)
      .then((batch) => setState({ kind: "ready", batch }))
      .catch((error: unknown) => setState({ kind: "error", error: toUiError(error) }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    generate();
  }

  return (
    <div className={styles.page}>
      <form className={styles.panel} onSubmit={handleSubmit}>
        <h2>
          Bộ đề <code>{blueprintId}</code>
        </h2>
        <div className={styles.row}>
          <label htmlFor="twin-count">Số câu song sinh cho mỗi câu gốc</label>
          <input
            id="twin-count"
            className={styles.number}
            type="number"
            min={MIN_TWIN_COUNT}
            max={MAX_TWIN_COUNT}
            value={count}
            onChange={(event) => setCount(clampTwinCount(event.target.valueAsNumber))}
          />
          <button type="submit" className={styles.button} disabled={state.kind === "loading"}>
            {state.kind === "loading" ? "Đang sinh…" : "Sinh câu hỏi song sinh"}
          </button>
        </div>
      </form>

      {state.kind === "idle" && (
        <p className={styles.muted}>Chưa sinh lượt nào cho bộ đề này. Chọn số câu rồi bấm "Sinh câu hỏi song sinh".</p>
      )}
      {state.kind === "loading" && <Loading label="Đang sinh và kiểm chứng câu hỏi…" />}
      {/* "Thử lại" gửi lại cùng yêu cầu với cùng khoá chống lặp, nên backend trả đúng lượt sinh cũ nếu đã sinh xong.
          Lỗi 409 (đề chưa duyệt) không có nút: gửi lại không đổi được kết quả. */}
      {state.kind === "error" && <ErrorNotice error={state.error} onRetry={generate} />}
      {state.kind === "ready" && <TwinViewer batch={state.batch} />}

      <p>
        <Link href="/twins">Mở bộ đề khác</Link>
      </p>
    </div>
  );
}
