"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { TableAuditor } from "@/components/ingestion/TableAuditor";
import { getItem } from "@/lib/api";
import type { IngestionItem, OptionKey } from "@/types/holocron";

const OPTION_KEYS: OptionKey[] = ["A", "B", "C", "D"];

type LoadState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; item: IngestionItem };

export default function IngestionReviewPage() {
  const params = useParams<{ itemId: string }>();
  const itemId = params.itemId;
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    getItem(itemId)
      .then((item) => {
        if (!cancelled) setState({ kind: "ready", item });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", message: err instanceof Error ? err.message : "Không tải được item." });
      });
    return () => {
      cancelled = true;
    };
  }, [itemId]);

  if (state.kind === "loading") {
    return (
      <main>
        <p>Đang tải item…</p>
      </main>
    );
  }
  if (state.kind === "error") {
    return (
      <main>
        <p role="alert">{state.message}</p>
        <Link href="/">Quay lại trang tải lên</Link>
      </main>
    );
  }

  const { item } = state;
  const setItem = (next: IngestionItem) => setState({ kind: "ready", item: next });

  return (
    <main>
      <h1>Kiểm duyệt đề {item.item_id}</h1>
      <section aria-label="Đề bài">
        <p>{item.stem_latex}</p>
        <ul>
          {OPTION_KEYS.map((key) => (
            <li key={key}>
              <strong>{key}.</strong> {item.options[key]}
              {item.self_solve?.matched_option_key === key && " — khớp kết quả tự giải"}
            </li>
          ))}
        </ul>
        {item.self_solve !== null && item.self_solve.solved_answer_display !== null && (
          <p>
            Sandbox tự giải trên bảng hiện tại: <strong>{item.self_solve.solved_answer_display}</strong> (
            {item.self_solve.status})
          </p>
        )}
      </section>

      <TableAuditor item={item} onItemChange={setItem} onApproved={setItem} />

      {item.approved && (
        <p role="status">
          Đã duyệt. Bảng đã được con người xác nhận và sẵn sàng cho bước sinh Twin Questions
          (<code>POST /v1/blueprints/{"{id}"}/twins</code>, xem SPECIFICATION §6.3).
        </p>
      )}
    </main>
  );
}
