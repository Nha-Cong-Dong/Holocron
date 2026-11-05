"use client";

import { useEffect, useRef, useState } from "react";

import type { PageView } from "@/lib/documents-data";
import {
  type ConfirmEvent,
  groupMismatchesByBlock,
  type MismatchPatch,
  mismatchCellLabel,
  reduceConfirm,
} from "@/lib/vlm-mismatch";
import vault from "./Vault.module.css";
import styles from "./VlmMismatchPanel.module.css";

interface VlmMismatchPanelProps {
  page: PageView;
  /** Có thao tác ghi khác đang chạy ở màn xem lại. */
  busy: boolean;
  /** Gửi `PATCH …/lines` với phần thay đổi; trả `true` khi thành công. */
  onPatch: (patch: MismatchPatch) => Promise<boolean>;
}

/**
 * "Chỗ lệch bảng do AI đọc" (ADR-009 mục 3.4): mỗi chỗ lệch có nút "Giữ giá trị này" riêng (không có "giữ tất cả"),
 * mỗi khối có nút "Bỏ khối AI đọc" (hỏi lại trước khi gửi). Chuỗi hiển thị là chuỗi backend trả, không so lại.
 */
export function VlmMismatchPanel({ page, busy, onPatch }: VlmMismatchPanelProps) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const askRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming !== null) confirmRef.current?.focus();
  }, [confirming]);
  const groups = groupMismatchesByBlock(page.vlmMismatches);
  if (groups.length === 0) {
    if (!page.reviewReasons.includes("TABLE_TOKEN_MISMATCH")) return null;
    return (
      <section className={vault.panel} aria-labelledby="vlm-mismatch-title">
        <h3 id="vlm-mismatch-title">Chỗ lệch bảng do AI đọc</h3>
        <p className={vault.muted} role="alert">
          Trang còn chỗ lệch bảng nhưng không đọc được chi tiết từng chỗ. Hãy tải lại trang; nếu vẫn vậy, báo lỗi.
        </p>
      </section>
    );
  }

  async function dispatch(event: ConfirmEvent): Promise<void> {
    const next = reduceConfirm({ confirming }, event);
    setConfirming(next.state.confirming);
    if (event.type === "cancel") askRef.current?.focus();
    if (next.patch === null) return;
    await onPatch(next.patch);
    titleRef.current?.focus();
  }

  return (
    <section className={vault.panel} aria-labelledby="vlm-mismatch-title">
      <h3 id="vlm-mismatch-title" ref={titleRef} tabIndex={-1}>
        Chỗ lệch bảng do AI đọc
      </h3>
      <p className={vault.muted}>
        AI và OCR đọc khác nhau ở các ô dưới đây. Giữ giá trị của AI từng chỗ một, hoặc bỏ cả khối AI đọc (khối trở về
        bản OCR). Trang chưa xác nhận được khi còn chỗ lệch.
      </p>
      {groups.map((group) => (
        <div key={group.blockId} className={styles.group}>
          <h4 className={styles.blockTitle}>Khối {group.blockId}</h4>
          <ul className={styles.list}>
            {group.items.map((item) => (
              <li key={item.mismatchId} className={styles.item}>
                <span className={styles.cell}>{mismatchCellLabel(item)}</span>
                <span>
                  AI đọc: <code>{item.vlmValue ?? "(không có)"}</code>
                </span>
                <span>
                  OCR: <code>{item.ocrValue ?? "(không có)"}</code>
                </span>
                <button
                  type="button"
                  className={`${vault.secondary} ${vault.small}`}
                  disabled={busy}
                  aria-label={`Giữ giá trị này: ${mismatchCellLabel(item)}, khối ${group.blockId}`}
                  onClick={() => void dispatch({ type: "keep", mismatchId: item.mismatchId })}
                >
                  Giữ giá trị này
                </button>
              </li>
            ))}
          </ul>
          {confirming === group.blockId ? (
            <div className={vault.actions}>
              <span>Bỏ khối này và dùng lại bản OCR?</span>
              <button
                type="button"
                className={`${vault.secondary} ${vault.small}`}
                disabled={busy}
                ref={confirmRef}
                onClick={() => void dispatch({ type: "confirmReject", blockId: group.blockId })}
              >
                Xác nhận bỏ khối
              </button>
              <button
                type="button"
                className={`${vault.secondary} ${vault.small}`}
                onClick={() => void dispatch({ type: "cancel" })}
              >
                Huỷ
              </button>
            </div>
          ) : (
            <div className={vault.actions}>
              <button
                type="button"
                className={`${vault.secondary} ${vault.small}`}
                disabled={busy}
                ref={askRef}
                onClick={() => void dispatch({ type: "askReject", blockId: group.blockId })}
              >
                Bỏ khối AI đọc
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
