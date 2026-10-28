"use client";

import type { ReactNode } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { CapabilityNoticeBox } from "@/components/common/CapabilityNotice";
import { TesseractHelp } from "@/components/common/TesseractHelp";
import { useShell } from "@/components/shell/ShellContext";
import { Accordion, Button, ErrorState } from "@/components/ui";
import { IS_DEVELOPMENT } from "@/lib/config";
import type { ErrorKind, UiError } from "@/lib/errors";
import {
  type CapabilityEntry,
  capabilitiesOf,
  capabilityLabel,
  capabilityOf,
  capabilityOverviewKind,
  type DataEgress,
  freshRuntime,
  groupCapabilities,
  needsTesseractHelp,
  type RuntimeView,
  selectDataEgress,
  type UnavailableGroup as UnavailableGroupName,
  unrecognizedGroupIntro,
} from "@/lib/runtime";

import styles from "./MachineDiagnostics.module.css";

/** Neo của mục "Máy của bạn" trong Cài đặt (dải "Xem cách cài" ở trang chủ trỏ tới đây). */
export const MACHINE_SECTION_ID = "may-cua-ban";

const GROUP_TEXT: Record<Exclude<UnavailableGroupName, "unrecognized">, { title: string; intro: string }> = {
  install: {
    title: "Cần cài thêm trên máy này",
    intro: "Các phần dưới đây chạy offline nhưng cần chương trình hoặc dữ liệu chưa có trên máy.",
  },
  configure: {
    title: "Cần cấu hình (tuỳ chọn)",
    intro: "Không cấu hình thì app vẫn chạy; chỉ các tính năng dưới đây tắt.",
  },
  not_implemented: {
    title: "Chưa có trong bản đang chạy",
    intro: "Các tính năng này sẽ có ở bản sau.",
  },
};

const UNRECOGNIZED_TITLE = "Giao diện chưa nhận ra";

const EGRESS_TEXT: Record<DataEgress, string> = {
  unknown: "Chưa biết có dữ liệu nào rời máy hay không, vì chưa kiểm tra được máy.",
  local_only: "Mọi thứ chạy trên máy này; không dữ liệu nào rời máy.",
  may_leave:
    "Đang bật dịch vụ ngoài: khi dùng các tính năng có gửi dữ liệu, văn bản hoặc ảnh trang được gửi tới dịch vụ đã cấu hình. Danh sách nằm trong Chi tiết kỹ thuật.",
};

const KEY_EGRESS_TEXT: Record<DataEgress, string> = {
  unknown:
    "Chưa đọc được trạng thái LLM và VLM của backend, nên chưa biết lúc này có dữ liệu nào có thể rời máy hay không.",
  local_only: "Hiện chưa bật LLM hay VLM: không dữ liệu nào rời máy.",
  may_leave:
    "Hiện đã bật LLM hoặc VLM: khi bạn dùng các tính năng ở nhóm “có gửi dữ liệu ra khỏi máy”, dữ liệu tương ứng được gửi tới dịch vụ đã cấu hình.",
};

/** Cách sửa khi không kiểm tra được máy, theo loại lỗi (không theo mã HTTP). */
function machineErrorFix(kind: ErrorKind): string {
  if (kind === "network" || kind === "server")
    return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.";
  if (kind === "forbidden") return "Holocron từ chối yêu cầu từ cửa sổ này. Mở lại Holocron trên máy rồi bấm Thử lại.";
  return "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.";
}

/** Lỗi không tới được phần chạy nền của Holocron (chưa chạy, sai địa chỉ). */
export function isUnreachable(error: UiError): boolean {
  return error.kind === "network" || error.kind === "server";
}

/** Mục thu gọn "Chi tiết kỹ thuật": nơi duy nhất được hiện mã lỗi và thuật ngữ kỹ thuật. */
export function TechnicalDetails({ children }: { children: ReactNode }) {
  return (
    <Accordion summary="Chi tiết kỹ thuật" className={styles.technical}>
      <div className={styles.technicalBody}>{children}</div>
    </Accordion>
  );
}

/** Hướng dẫn khởi động backend (chỉ hiện trong "Chi tiết kỹ thuật"). */
export function BackendHelp() {
  return (
    <div className={styles.help}>
      <p>
        Backend của Holocron chưa chạy hoặc chưa sẵn sàng. Khởi động backend, rồi bấm "Thử lại" ở trên hoặc "Kiểm tra
        lại" ở thanh trạng thái.
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

/**
 * Cài đặt › Máy của bạn (design-handoff/README.md màn 11): máy đã đọc được ảnh scan chưa, ba bước cài Tesseract khi
 * thiếu, dữ liệu có rời máy không, nút "Kiểm tra lại". Danh sách khả năng, khoá API và mã lỗi nằm trong "Chi tiết kỹ
 * thuật". Chỉ hiện khả năng khi lần kiểm tra gần nhất thành công (dữ liệu cũ không trình bày như hiện tại).
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
        <>
          <ErrorState
            title="Không kiểm tra được máy"
            fix={machineErrorFix(runtime.error.kind)}
            onRetry={refreshRuntime}
            busy={checking}
          />
          <TechnicalDetails>
            <p>{runtime.error.message}</p>
            {runtime.error.diagnostic !== undefined && <code className={styles.code}>{runtime.error.diagnostic}</code>}
            {runtime.runtime !== null && (
              <p className={styles.muted}>
                Danh sách khả năng của lần kiểm tra trước đã được ẩn vì chưa cập nhật được; nó sẽ hiện lại khi backend
                trả lời.
              </p>
            )}
            {isUnreachable(runtime.error) && <BackendHelp />}
          </TechnicalDetails>
        </>
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
          <TechnicalDetails>
            <CapabilityOverview runtime={current} />
            <KeyPolicy egress={selectDataEgress(current)} />
          </TechnicalDetails>
        </>
      )}
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

function CapabilityOverview({ runtime }: { runtime: RuntimeView }) {
  const capabilities = capabilitiesOf(runtime) ?? [];
  const kind = capabilityOverviewKind(runtime);
  if (kind === "empty") {
    return (
      <div className={styles.group}>
        <h3 className={styles.subheading}>Khả năng của máy này</h3>
        <p className={styles.muted}>
          Backend trả về danh sách khả năng rỗng (hoặc giao diện không đọc được phần tử nào), nên chưa biết tính năng
          nào chạy được trên máy này. Các tính năng vẫn mở; nếu một thao tác báo thiếu thành phần, làm theo hướng dẫn
          hiện ngay tại đó. Bấm “Kiểm tra lại” sau khi cập nhật backend.
        </p>
        <ul className={styles.facts}>
          <li>LLM: {runtime.llm_configured ? "đã cấu hình" : "chưa cấu hình, app chạy offline"}.</li>
          <li>Mô hình đọc ảnh (VLM): {runtime.vlm_configured ? "đã cấu hình" : "chưa cấu hình"}.</li>
        </ul>
      </div>
    );
  }
  if (kind === "legacy") {
    return (
      <div className={styles.group}>
        <h3 className={styles.subheading}>Khả năng của máy này</h3>
        <p className={styles.muted}>Backend đang chạy là bản cũ, chưa báo danh sách khả năng. Những gì biết được:</p>
        <ul className={styles.facts}>
          <li>LLM: {runtime.llm_configured ? "đã cấu hình" : "chưa cấu hình, app chạy offline"}.</li>
          <li>Mô hình đọc ảnh (VLM): {runtime.vlm_configured ? "đã cấu hình" : "chưa cấu hình"}.</li>
        </ul>
        <TesseractHelp headingLevel={3} />
      </div>
    );
  }

  const groups = groupCapabilities(capabilities);
  return (
    <div className={styles.group}>
      <h3 className={styles.subheading}>Khả năng của máy này</h3>
      <ReadyGroup
        title="Chạy được ngay, không cần mạng"
        capabilities={groups.offline}
        badge="sẵn sàng"
        tone="success"
        note={null}
      />
      <ReadyGroup
        title="Đang dùng được, có gửi dữ liệu ra khỏi máy"
        capabilities={groups.online}
        badge="dùng mạng"
        tone="info"
        note="Khi dùng các tính năng này, văn bản đã tách từ tài liệu (với LLM) hoặc ảnh trang (với VLM) được gửi tới dịch vụ đã cấu hình."
      />
      <UnavailableGroup {...GROUP_TEXT.install} capabilities={groups.install} />
      {needsTesseractHelp(runtime) && <TesseractHelp headingLevel={3} />}
      <UnavailableGroup {...GROUP_TEXT.configure} capabilities={groups.configure} />
      <UnavailableGroup {...GROUP_TEXT.not_implemented} capabilities={groups.not_implemented} />
      <UnavailableGroup
        title={UNRECOGNIZED_TITLE}
        intro={unrecognizedGroupIntro(groups.unrecognized)}
        capabilities={groups.unrecognized}
      />
    </div>
  );
}

/** Khoá React cho một phần tử: tên có thể trùng hoặc là `null` (tên lạ), nên kèm vị trí. */
function entryKey(entry: CapabilityEntry, index: number): string {
  return `${entry.name ?? "unrecognized"}-${index}`;
}

interface ReadyGroupProps {
  title: string;
  capabilities: CapabilityEntry[];
  badge: string;
  tone: "success" | "info";
  note: string | null;
}

function ReadyGroup({ title, capabilities, badge, tone, note }: ReadyGroupProps) {
  if (capabilities.length === 0) return null;
  return (
    <div className={styles.group}>
      <h4 className={styles.groupTitle}>{title}</h4>
      {note !== null && <p className={styles.muted}>{note}</p>}
      <ul className={styles.readyList}>
        {capabilities.map((capability, index) => (
          <li key={entryKey(capability, index)}>
            <span className={styles.badge} data-tone={tone}>
              {badge}
            </span>{" "}
            {capabilityLabel(capability)}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface UnavailableGroupProps {
  title: string;
  intro: string;
  capabilities: CapabilityEntry[];
}

function UnavailableGroup({ title, intro, capabilities }: UnavailableGroupProps) {
  if (capabilities.length === 0) return null;
  return (
    <div className={styles.group}>
      <h4 className={styles.groupTitle}>{title}</h4>
      <p className={styles.muted}>{intro}</p>
      <div className={styles.notices}>
        {capabilities.map((capability, index) => (
          <CapabilityNoticeBox
            key={entryKey(capability, index)}
            label={capabilityLabel(capability)}
            available={capability.available}
            detail={capability.detail_vi}
          />
        ))}
      </div>
    </div>
  );
}

function KeyPolicy({ egress }: { egress: DataEgress }) {
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
