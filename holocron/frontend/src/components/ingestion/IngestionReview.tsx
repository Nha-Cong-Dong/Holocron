"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import type { IngestionItem, OptionKey } from "@/types/holocron";

import styles from "./IngestionView.module.css";
import { MockExtractorNotice } from "./MockExtractorNotice";
import { TableAuditor } from "./TableAuditor";

const OPTION_KEYS: OptionKey[] = ["A", "B", "C", "D"];

type LoadState = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; item: IngestionItem };

/** Kiểm duyệt một ingestion item (`/ingestion?item=…`): đề, phương án, kết quả tự giải và `TableAuditor`. */
export function IngestionReview({ itemId }: { itemId: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const load = useCallback(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    api
      .getItem(itemId)
      .then((item) => {
        if (!cancelled) setState({ kind: "ready", item });
      })
      .catch((error: unknown) => {
        if (!cancelled) setState({ kind: "error", error: toUiError(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [itemId]);

  useEffect(() => load(), [load]);

  if (state.kind === "loading") {
    return <Loading label="Đang tải đề…" />;
  }
  if (state.kind === "error") {
    return (
      <div className={styles.page}>
        <ErrorNotice error={state.error} onAction={load} />
        <p>
          <Link href="/ingestion">Quay lại trang tải đề</Link>
        </p>
      </div>
    );
  }

  const { item } = state;
  const setItem = (next: IngestionItem) => setState({ kind: "ready", item: next });

  return (
    <div className={styles.page}>
      <MockExtractorNotice />
      <section className={styles.panel} aria-labelledby="item-stem">
        <h2 id="item-stem">
          Kiểm duyệt đề <code>{item.item_id}</code>
        </h2>
        <p>{item.stem_latex}</p>
        <ul>
          {OPTION_KEYS.map((key) => (
            <li key={key}>
              <strong>{key}.</strong> {item.options[key]}
              {item.self_solve?.matched_option_key === key && " (khớp kết quả tự giải)"}
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
        <p className={styles.success}>Đã duyệt. Bảng đã được xác nhận và sẵn sàng cho bước sinh câu hỏi song sinh.</p>
      )}
    </div>
  );
}
