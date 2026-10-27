"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { EmptyState } from "@/components/common/LoadState";

import { TwinBatchPanel } from "./TwinBatchPanel";
import styles from "./TwinsView.module.css";

/**
 * `/twins?blueprint=…`: sinh câu hỏi song sinh cho một bộ đề. ID đi qua query string để build được ở chế độ static
 * export (kế hoạch UI mục 4.3). Phải nằm trong `<Suspense>`.
 */
export function TwinsView() {
  const blueprintId = useSearchParams().get("blueprint")?.trim() ?? "";
  if (blueprintId === "") {
    return <BlueprintPicker />;
  }
  return <TwinBatchPanel key={blueprintId} blueprintId={blueprintId} />;
}

function BlueprintPicker() {
  const router = useRouter();
  const [draft, setDraft] = useState("");

  function open(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const id = draft.trim();
    if (id !== "") {
      router.push(`/twins?blueprint=${encodeURIComponent(id)}`);
    }
  }

  return (
    <EmptyState title="Chưa chọn bộ đề nào.">
      <form className={styles.picker} onSubmit={open}>
        <label htmlFor="blueprint-id">Nhập mã bộ đề đã tạo để sinh câu hỏi song sinh</label>
        <div className={styles.row}>
          <input
            id="blueprint-id"
            className={styles.input}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
          <button type="submit" className={styles.button} disabled={draft.trim() === ""}>
            Mở bộ đề
          </button>
        </div>
      </form>
    </EmptyState>
  );
}
