import { CAPABILITY_LABEL_VI, capabilityOf } from "@/lib/runtime";
import type { CapabilityName, RuntimeInfoPending } from "@/types/contract-pending";

import styles from "./CapabilityNotice.module.css";

interface CapabilityNoticeProps {
  runtime: RuntimeInfoPending | null;
  name: CapabilityName;
}

/**
 * Báo một khả năng không khả dụng ngay tại màn hình dùng nó, kèm `detail_vi` của backend (§5.12). Khả dụng hoặc
 * backend chưa báo thì không hiện gì, để không chặn phần khác của màn hình.
 */
export function CapabilityNotice({ runtime, name }: CapabilityNoticeProps) {
  const view = capabilityOf(runtime, name);
  if (view.state !== "unavailable") return null;
  return (
    <div className={styles.notice}>
      <p className={styles.title}>{CAPABILITY_LABEL_VI[name]}: chưa dùng được</p>
      {view.detail !== null && <p className={styles.detail}>{view.detail}</p>}
    </div>
  );
}
