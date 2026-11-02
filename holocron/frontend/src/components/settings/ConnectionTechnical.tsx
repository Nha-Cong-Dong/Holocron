"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";

import { Accordion, Button } from "@/components/ui";
import { IS_DEVELOPMENT, USE_MOCKS } from "@/lib/config";
import type { UiError } from "@/lib/errors";
import {
  buildLabel,
  COPY_FEEDBACK_MS,
  COPY_LABEL_VI,
  type CopyState,
  copyText,
  type DataEgress,
  logDirOf,
  type RuntimeState,
  selectStatusItems,
} from "@/lib/runtime";

import styles from "./MachineDiagnostics.module.css";

/**
 * Phần kỹ thuật của Cài đặt › Máy của bạn: chỉ hiện trong mục thu gọn "Chi tiết kỹ thuật" (nơi duy nhất được dùng
 * thuật ngữ kỹ thuật và mã lỗi). Gồm trạng thái kết nối (thay cho thanh trạng thái dưới cùng đã gỡ), bản đang chạy,
 * thư mục log, cách khởi động phần chạy nền và chính sách khoá API.
 */

/** Mục thu gọn "Chi tiết kỹ thuật": nơi duy nhất được hiện mã lỗi và thuật ngữ kỹ thuật. */
export function TechnicalDetails({ children }: { children: ReactNode }) {
  return (
    <Accordion summary="Chi tiết kỹ thuật" className={styles.technical}>
      <div className={styles.technicalBody}>{children}</div>
    </Accordion>
  );
}

/** Lỗi không tới được phần chạy nền của Holocron (chưa chạy, sai địa chỉ). */
export function isUnreachable(error: UiError): boolean {
  return error.kind === "network" || error.kind === "server";
}

/** Hướng dẫn khởi động backend (chỉ hiện trong "Chi tiết kỹ thuật"). */
export function BackendHelp() {
  return (
    <div className={styles.help}>
      <p>
        Backend của Holocron chưa chạy hoặc chưa sẵn sàng. Khởi động backend, rồi bấm "Thử lại" ở trên hoặc "Kiểm tra
        lại" trong Cài đặt › Máy của bạn.
      </p>
      {IS_DEVELOPMENT && (
        <>
          <p className={styles.muted}>
            Bản phát triển: chạy backend trong thư mục <code>holocron/backend</code> bằng lệnh
          </p>
          <pre className={styles.command}>python -m uvicorn holocron_core.routers.api:app --reload</pre>
        </>
      )}
    </div>
  );
}

/** Lần kiểm tra máy gần nhất lỗi: thông điệp, mã chẩn đoán và cách khởi động backend khi không tới được. */
export function ConnectionErrorTechnical({ error, hadRuntime }: { error: UiError; hadRuntime: boolean }) {
  return (
    <div className={styles.group}>
      <h3 className={styles.subheading}>Lỗi kiểm tra gần nhất</h3>
      <p>{error.message}</p>
      {error.diagnostic !== undefined && <code className={styles.code}>{error.diagnostic}</code>}
      {hadRuntime && (
        <p className={styles.muted}>
          Danh sách khả năng của lần kiểm tra trước đã được ẩn vì chưa cập nhật được; nó sẽ hiện lại khi backend trả
          lời.
        </p>
      )}
      {isUnreachable(error) && <BackendHelp />}
    </div>
  );
}

/**
 * Trạng thái kết nối (backend, LLM, OCR, chế độ offline, dữ liệu mẫu: `selectStatusItems`), nhãn bản đang chạy và thư
 * mục log kèm nút "Sao chép". Mỗi ô có chữ, không chỉ dựa vào màu.
 */
export function ConnectionStatusTechnical({ runtime }: { runtime: RuntimeState }) {
  const items = selectStatusItems(runtime, USE_MOCKS);
  const build = buildLabel(runtime.runtime);
  const logDir = logDirOf(runtime.runtime);
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  function copyLogDir(): void {
    if (logDir === null) return;
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    void copyText(clipboard, logDir).then((state) => {
      setCopyState(state);
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopyState("idle"), COPY_FEEDBACK_MS);
    });
  }

  return (
    <div className={styles.group}>
      <h3 className={styles.subheading}>Trạng thái kết nối</h3>
      <ul className={styles.statusList} aria-label="Trạng thái hệ thống">
        {items.map((item) => (
          <li key={item.id} className={styles.statusItem} data-tone={item.tone}>
            <span className={styles.statusDot} aria-hidden="true" />
            <span className={styles.muted}>{item.label}:</span> <span>{item.value}</span>
          </li>
        ))}
      </ul>
      {build !== null && <p>Bản đang chạy: {build}</p>}
      {logDir !== null && (
        <div className={styles.logDir}>
          <span className={styles.muted}>Thư mục log:</span>
          <code className={styles.code} title={logDir}>
            {logDir}
          </code>
          <Button variant="secondary" onClick={copyLogDir} aria-label="Sao chép thư mục log">
            {COPY_LABEL_VI[copyState]}
          </Button>
          <span className="visually-hidden" aria-live="polite">
            {copyState === "idle" ? "" : COPY_LABEL_VI[copyState]}
          </span>
        </div>
      )}
    </div>
  );
}

const KEY_EGRESS_TEXT: Record<DataEgress, string> = {
  unknown:
    "Chưa đọc được trạng thái LLM và VLM của backend, nên chưa biết lúc này có dữ liệu nào có thể rời máy hay không.",
  local_only: "Hiện chưa bật LLM hay VLM: không dữ liệu nào rời máy.",
  may_leave:
    "Hiện đã bật LLM hoặc VLM: khi bạn dùng các tính năng ở nhóm “có gửi dữ liệu ra khỏi máy”, dữ liệu tương ứng được gửi tới dịch vụ đã cấu hình.",
};

/** Chính sách khoá API: giao diện không bao giờ nhận khoá; dữ liệu chỉ rời máy khi bật LLM hoặc VLM. */
export function KeyPolicy({ egress }: { egress: DataEgress }) {
  return (
    <div className={styles.group}>
      <h3 className={styles.subheading}>Khoá API và dữ liệu của bạn</h3>
      <p>
        Holocron không bao giờ hỏi, nhận hay lưu khoá API trong giao diện. Muốn dùng LLM, đặt khoá bằng biến môi trường
        của backend rồi khởi động lại backend; bản desktop sẽ dùng kho khoá của hệ điều hành.
      </p>
      <p className={styles.muted}>
        Dữ liệu chỉ rời máy khi bật LLM hoặc VLM: văn bản đã tách từ tài liệu được gửi tới LLM đã cấu hình, ảnh trang
        được gửi tới VLM đã cấu hình. Không bật cả hai thì không dữ liệu nào rời máy.
      </p>
      <p className={styles.muted}>{KEY_EGRESS_TEXT[egress]}</p>
    </div>
  );
}
