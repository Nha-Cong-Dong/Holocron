import { CAPABILITY_LABEL_VI, capabilityOf, type RuntimeView } from "@/lib/runtime";
import type { CapabilityName } from "@/types/holocron";

import styles from "./CapabilityNotice.module.css";

interface CapabilityNoticeProps {
  runtime: RuntimeView | null;
  name: CapabilityName;
}

/**
 * Báo một khả năng không khả dụng ngay tại màn hình dùng nó, kèm `detail_vi` của backend (§5.12). Khả dụng hoặc
 * backend chưa báo thì không hiện gì, để không chặn phần khác của màn hình.
 */
export function CapabilityNotice({ runtime, name }: CapabilityNoticeProps) {
  const view = capabilityOf(runtime, name);
  if (view.state !== "unavailable") return null;
  return <CapabilityNoticeBox label={CAPABILITY_LABEL_VI[name]} available={false} detail={view.detail} />;
}

interface CapabilityNoticeBoxProps {
  label: string;
  /** Khả năng đang bật (chỉ gặp ở nhóm "giao diện chưa nhận ra"). */
  available: boolean;
  detail: string | null;
}

/** Khối thông báo dùng chung: nhãn khả năng, trạng thái bằng chữ và câu hướng dẫn `detail_vi` (nếu backend có gửi). */
export function CapabilityNoticeBox({ label, available, detail }: CapabilityNoticeBoxProps) {
  return (
    <div className={styles.notice}>
      <p className={styles.title}>
        {label}: {available ? "đang bật" : "chưa dùng được"}
      </p>
      {detail !== null && <p className={styles.detail}>{detail}</p>}
    </div>
  );
}
