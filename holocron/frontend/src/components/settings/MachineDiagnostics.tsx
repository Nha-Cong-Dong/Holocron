"use client";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { useShell } from "@/components/shell/ShellContext";
import { Button, EmptyState, ErrorState } from "@/components/ui";
import type { ErrorKind } from "@/lib/errors";
import {
  capabilityOf,
  type DataEgress,
  freshRuntime,
  needsTesseractHelp,
  type RuntimeView,
  selectDataEgress,
} from "@/lib/runtime";

import { CapabilityOverview } from "./CapabilityTechnical";
import {
  ConnectionErrorTechnical,
  ConnectionStatusTechnical,
  KeyPolicy,
  TechnicalDetails,
} from "./ConnectionTechnical";
import styles from "./MachineDiagnostics.module.css";

// Trang chủ (`HomeScreen`) dùng các phần này qua đường dẫn cũ.
export { BackendHelp, isUnreachable, TechnicalDetails } from "./ConnectionTechnical";

/** Neo của mục "Máy của bạn" trong Cài đặt (dải "Xem cách cài" ở trang chủ trỏ tới đây). */
export const MACHINE_SECTION_ID = "may-cua-ban";

const EGRESS_TEXT: Record<DataEgress, string> = {
  unknown: "Chưa biết có dữ liệu nào rời máy hay không, vì chưa kiểm tra được máy.",
  local_only: "Mọi thứ chạy trên máy này; không dữ liệu nào rời máy.",
  may_leave:
    "Đang bật dịch vụ ngoài: khi dùng các tính năng có gửi dữ liệu, văn bản hoặc ảnh trang được gửi tới dịch vụ đã cấu hình. Danh sách nằm trong Chi tiết kỹ thuật.",
};

/** Cách sửa khi không kiểm tra được máy, theo loại lỗi (không theo mã HTTP). */
function machineErrorFix(kind: ErrorKind): string {
  if (kind === "network" || kind === "server")
    return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.";
  if (kind === "forbidden") return "Holocron từ chối yêu cầu từ cửa sổ này. Mở lại Holocron trên máy rồi bấm Thử lại.";
  return "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.";
}

/**
 * Cài đặt › Máy của bạn (design-handoff/README.md màn 11): máy đã đọc được ảnh scan chưa, ba bước cài Tesseract khi
 * thiếu, dữ liệu có rời máy không, nút "Kiểm tra lại". Bốn trạng thái: đang kiểm tra (khối Holocron xoay), lỗi (việc gì
 * xảy ra, cách sửa, "Thử lại"), chưa kiểm tra (một hành động "Kiểm tra máy"), đã kiểm tra. Trạng thái kết nối, bản đang
 * chạy, thư mục log, danh sách khả năng, khoá API và mã lỗi nằm trong "Chi tiết kỹ thuật". Chỉ hiện khả năng khi lần
 * kiểm tra gần nhất thành công (dữ liệu cũ không trình bày như hiện tại).
 */
export function MachineDiagnostics() {
  const { runtime, refreshRuntime } = useShell();
  const current = freshRuntime(runtime);
  const checking = runtime.backend === "connecting";

  return (
    <section id={MACHINE_SECTION_ID} className={styles.panel} aria-labelledby="machine-title">
      <div>
        <h2 id="machine-title" className={styles.heading}>
          Máy của bạn
        </h2>
        <p className={styles.caption}>Kiểm tra máy đã sẵn sàng đọc mọi loại PDF chưa.</p>
      </div>

      {checking && (
        <output className={styles.checking}>
          <HolocronMark size={22} spinning />
          Đang kiểm tra máy…
        </output>
      )}

      {runtime.error !== null && (
        <ErrorState
          title="Không kiểm tra được máy"
          fix={machineErrorFix(runtime.error.kind)}
          onRetry={refreshRuntime}
          busy={checking}
        />
      )}

      {!checking && runtime.error === null && current === null && (
        <EmptyState
          title="Chưa kiểm tra máy"
          description="Kiểm tra một lần để biết máy đã sẵn sàng đọc mọi loại PDF chưa."
          action={{ label: "Kiểm tra máy", onClick: refreshRuntime }}
        />
      )}

      {current !== null && (
        <>
          <ScanReadiness runtime={current} />
          <p className={styles.egress}>{EGRESS_TEXT[selectDataEgress(current)]}</p>
          <div className={styles.actions}>
            <Button variant="secondary" busy={checking} busyLabel="Đang kiểm tra" onClick={refreshRuntime}>
              Kiểm tra lại
            </Button>
          </div>
        </>
      )}

      <TechnicalDetails>
        {runtime.error !== null && (
          <ConnectionErrorTechnical error={runtime.error} hadRuntime={runtime.runtime !== null} />
        )}
        <ConnectionStatusTechnical runtime={runtime} />
        {current !== null && (
          <>
            <CapabilityOverview runtime={current} />
            <KeyPolicy egress={selectDataEgress(current)} />
          </>
        )}
      </TechnicalDetails>
    </section>
  );
}

/**
 * Máy đọc được ảnh scan chưa; thiếu Tesseract thì ba bước cài bằng lời thường (tên tệp, thư mục, biến môi trường nằm
 * trong "Chi tiết kỹ thuật").
 */
function ScanReadiness({ runtime }: { runtime: RuntimeView }) {
  const ocr = capabilityOf(runtime, "ocr");
  if (ocr.state === "available") {
    return <p className={styles.ready}>Máy đã sẵn sàng đọc cả PDF có chữ sẵn và trang scan.</p>;
  }
  if (needsTesseractHelp(runtime)) {
    return (
      <div className={styles.warn}>
        <p className={styles.warnTitle}>Chưa đọc được ảnh scan. File PDF có chữ sẵn vẫn đọc bình thường.</p>
        <ol className={styles.steps}>
          <li>Cài chương trình Tesseract cho máy này (chương trình đọc chữ trong ảnh scan).</li>
          <li>Cài thêm gói tiếng Việt cho Tesseract. Tên tệp và thư mục cần chép nằm trong “Chi tiết kỹ thuật”.</li>
          <li>Mở lại Holocron, rồi bấm “Kiểm tra lại” ở dưới.</li>
        </ol>
      </div>
    );
  }
  if (ocr.state === "unknown") {
    return (
      <p className={styles.caption}>
        Chưa biết máy đã đọc được ảnh scan chưa. File PDF có chữ sẵn vẫn đọc bình thường.
      </p>
    );
  }
  return (
    <p className={styles.caption}>Bản đang chạy chưa đọc được ảnh scan. File PDF có chữ sẵn vẫn đọc bình thường.</p>
  );
}
