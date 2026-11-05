"use client";

import { useState } from "react";

import type { PageView } from "@/lib/documents-data";
import {
  groupMismatchesByBlock,
  keepMismatchPatch,
  type MismatchPatch,
  mismatchCellLabel,
  rejectBlockPatch,
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
  const groups = groupMismatchesByBlock(page.vlmMismatches);
  if (groups.length === 0) return null;

  async function send(patch: MismatchPatch): Promise<void> {
    setConfirming(null);
    await onPatch(patch);
  }

  return (
    <section className={vault.panel} aria-labelledby="vlm-mismatch-title">
      <h3 id="vlm-mismatch-title">Chỗ lệch bảng do AI đọc</h3>
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
                  onClick={() => void send(keepMismatchPatch(item.mismatchId))}
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
                onClick={() => void send(rejectBlockPatch(group.blockId))}
              >
                Xác nhận bỏ khối
              </button>
              <button type="button" className={`${vault.secondary} ${vault.small}`} onClick={() => setConfirming(null)}>
                Huỷ
              </button>
            </div>
          ) : (
            <div className={vault.actions}>
              <button
                type="button"
                className={`${vault.secondary} ${vault.small}`}
                disabled={busy}
                onClick={() => setConfirming(group.blockId)}
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
