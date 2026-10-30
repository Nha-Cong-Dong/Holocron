"use client";

import { CapabilityNoticeBox } from "@/components/common/CapabilityNotice";
import { TesseractHelp } from "@/components/common/TesseractHelp";
import {
  type CapabilityEntry,
  capabilitiesOf,
  capabilityLabel,
  capabilityOverviewKind,
  groupCapabilities,
  needsTesseractHelp,
  type RuntimeView,
  type UnavailableGroup as UnavailableGroupName,
  unrecognizedGroupIntro,
} from "@/lib/runtime";

import styles from "./MachineDiagnostics.module.css";

/**
 * Danh sách khả năng của máy và cấu hình mô hình ngôn ngữ, chỉ hiện trong "Chi tiết kỹ thuật" của Cài đặt (nơi duy
 * nhất được dùng tên kỹ thuật như LLM, VLM, OCR).
 */

const GROUP_TEXT: Record<Exclude<UnavailableGroupName, "unrecognized">, { title: string; intro: string }> = {
  pending: {
    title: "Đang kiểm tra trên máy này",
    intro: "Holocron đang kiểm tra OCR, vài giây nữa là xong. Trạng thái tự cập nhật.",
  },
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

/** Danh sách khả năng theo nhóm (chạy offline, có gửi dữ liệu, cần cài, cần cấu hình, chưa có, chưa nhận ra). */
export function CapabilityOverview({ runtime }: { runtime: RuntimeView }) {
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
        <ModelFacts runtime={runtime} />
      </div>
    );
  }
  if (kind === "legacy") {
    return (
      <div className={styles.group}>
        <h3 className={styles.subheading}>Khả năng của máy này</h3>
        <p className={styles.muted}>Backend đang chạy là bản cũ, chưa báo danh sách khả năng. Những gì biết được:</p>
        <ModelFacts runtime={runtime} />
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
      <UnavailableGroup {...GROUP_TEXT.pending} capabilities={groups.pending} />
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

function ModelFacts({ runtime }: { runtime: RuntimeView }) {
  return (
    <ul className={styles.facts}>
      <li>LLM: {runtime.llm_configured ? "đã cấu hình" : "chưa cấu hình, app chạy offline"}.</li>
      <li>Mô hình đọc ảnh (VLM): {runtime.vlm_configured ? "đã cấu hình" : "chưa cấu hình"}.</li>
    </ul>
  );
}

/**
 * Cách bật "Câu hỏi tự nhiên hơn": trạng thái LLM/VLM backend báo và các biến môi trường cần đặt. `runtime` là `null`
 * khi chưa kiểm tra được máy.
 */
export function LanguageModelTechnical({ runtime }: { runtime: RuntimeView | null }) {
  return (
    <div className={styles.group}>
      <h3 className={styles.subheading}>Mô hình ngôn ngữ (LLM)</h3>
      {runtime === null ? (
        <p className={styles.muted}>Chưa đọc được trạng thái LLM của backend. Bấm “Kiểm tra lại” ở mục Máy của bạn.</p>
      ) : (
        <ModelFacts runtime={runtime} />
      )}
      <p>
        Cách bật: đặt đủ ba biến môi trường <code className={styles.code}>HOLOCRON_LLM_BASE_URL</code>,{" "}
        <code className={styles.code}>HOLOCRON_LLM_API_KEY</code> và{" "}
        <code className={styles.code}>HOLOCRON_LLM_MODEL_EXTRACT</code> cho backend rồi khởi động lại Holocron. Thiếu
        một biến thì LLM vẫn tắt và không có gì được gửi ra mạng.
      </p>
      <p className={styles.muted}>
        Khi bật, văn bản đã tách từ tài liệu được gửi tới dịch vụ LLM đã cấu hình để soạn câu hỏi. Tắt đi, app vẫn ra đề
        bình thường.
      </p>
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
