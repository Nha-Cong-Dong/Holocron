"use client";

import Link from "next/link";

import { CapabilityNotice } from "@/components/common/CapabilityNotice";
import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import {
  CAPABILITY_LABEL_VI,
  type CapabilityGroup,
  capabilitiesOf,
  groupCapabilities,
  needsTesseractHelp,
} from "@/lib/runtime";
import type { Capability, RuntimeInfoPending } from "@/types/contract-pending";

import styles from "./StartScreen.module.css";

const GROUP_TEXT: Record<Exclude<CapabilityGroup, "offline" | "online">, { title: string; intro: string }> = {
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

/** Màn chạy lần đầu (trang gốc): đọc `capabilities` để nói rõ cái gì chạy offline được, cái gì cần cài thêm. */
export function StartScreen() {
  const { runtime, refreshRuntime } = useShell();

  return (
    <div className={styles.screen}>
      <section className={styles.intro}>
        <h2>Chào mừng tới Holocron</h2>
        <p>
          Holocron đọc tài liệu môn học của bạn, dựng kho tri thức có trích dẫn nguyên văn và sinh câu hỏi luyện tập có
          kiểm chứng. Mọi con số trong câu hỏi do backend tính; giao diện chỉ hiển thị.
        </p>
      </section>

      {runtime.runtime === null && runtime.backend === "connecting" && (
        <Loading label="Đang kiểm tra backend và khả năng của máy này…" />
      )}

      {runtime.runtime === null && runtime.error !== null && (
        <section className={styles.section} aria-labelledby="start-backend">
          <h2 id="start-backend">Chưa kết nối được backend</h2>
          <ErrorNotice error={runtime.error} onAction={refreshRuntime} />
          <BackendHelp />
        </section>
      )}

      {runtime.runtime !== null && <CapabilityOverview runtime={runtime.runtime} />}

      <KeyPolicy />

      <section className={styles.section} aria-labelledby="start-next">
        <h2 id="start-next">Bước tiếp theo</h2>
        <ul className={styles.nextSteps}>
          <li>
            <Link href="/ingestion">Nhập một đề thi</Link>: tải ảnh hoặc PDF đề, kiểm duyệt bảng số liệu trước khi sinh
            câu song sinh.
          </li>
          <li>
            Nhấn <kbd>Ctrl+K</kbd> để đi nhanh tới màn khác, hoặc <kbd>?</kbd> để xem toàn bộ phím tắt.
          </li>
        </ul>
      </section>
    </div>
  );
}

function BackendHelp() {
  return (
    <div className={styles.help}>
      <p>
        Chạy backend trong thư mục <code>holocron/backend</code>:
      </p>
      <pre className={styles.command}>python -m uvicorn holocron_core.routers.api:app --host 127.0.0.1 --port 8000</pre>
      <p>Rồi bấm "Thử lại" ở trên hoặc "Kiểm tra lại" ở thanh trạng thái.</p>
    </div>
  );
}

function CapabilityOverview({ runtime }: { runtime: RuntimeInfoPending }) {
  const capabilities = capabilitiesOf(runtime);
  if (capabilities === null) {
    return (
      <section className={styles.section} aria-labelledby="start-capabilities">
        <h2 id="start-capabilities">Khả năng của máy này</h2>
        <p className={styles.muted}>Backend đang chạy là bản cũ, chưa báo danh sách khả năng. Những gì biết được:</p>
        <ul className={styles.facts}>
          <li>LLM: {runtime.llm_configured ? "đã cấu hình" : "chưa cấu hình, app chạy offline"}.</li>
          <li>Mô hình đọc ảnh (VLM): {runtime.vlm_configured ? "đã cấu hình" : "chưa cấu hình"}.</li>
        </ul>
        <TesseractHelp />
      </section>
    );
  }

  const groups = groupCapabilities(capabilities);
  return (
    <section className={styles.section} aria-labelledby="start-capabilities">
      <h2 id="start-capabilities">Khả năng của máy này</h2>
      <ReadyGroup
        title="Chạy được ngay, không cần mạng"
        capabilities={groups.offline}
        badge="sẵn sàng"
        tone="success"
        note={null}
      />
      <ReadyGroup
        title="Đang dùng được, có gửi dữ liệu tới LLM"
        capabilities={groups.online}
        badge="dùng LLM"
        tone="info"
        note="Khi dùng các tính năng này, văn bản đã tách từ tài liệu được gửi tới dịch vụ LLM đã cấu hình."
      />
      <UnavailableGroup group="install" capabilities={groups.install} runtime={runtime} />
      {needsTesseractHelp(runtime) && <TesseractHelp />}
      <UnavailableGroup group="configure" capabilities={groups.configure} runtime={runtime} />
      <UnavailableGroup group="not_implemented" capabilities={groups.not_implemented} runtime={runtime} />
    </section>
  );
}

interface ReadyGroupProps {
  title: string;
  capabilities: Capability[];
  badge: string;
  tone: "success" | "info";
  note: string | null;
}

function ReadyGroup({ title, capabilities, badge, tone, note }: ReadyGroupProps) {
  if (capabilities.length === 0) return null;
  return (
    <div className={styles.group}>
      <h3>{title}</h3>
      {note !== null && <p className={styles.muted}>{note}</p>}
      <ul className={styles.ready}>
        {capabilities.map((capability) => (
          <li key={capability.name}>
            <span className={styles.badge} data-tone={tone}>
              {badge}
            </span>{" "}
            {CAPABILITY_LABEL_VI[capability.name]}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface UnavailableGroupProps {
  group: Exclude<CapabilityGroup, "offline" | "online">;
  capabilities: Capability[];
  runtime: RuntimeInfoPending;
}

function UnavailableGroup({ group, capabilities, runtime }: UnavailableGroupProps) {
  if (capabilities.length === 0) return null;
  const text = GROUP_TEXT[group];
  return (
    <div className={styles.group}>
      <h3>{text.title}</h3>
      <p className={styles.muted}>{text.intro}</p>
      <div className={styles.notices}>
        {capabilities.map((capability) => (
          <CapabilityNotice key={capability.name} runtime={runtime} name={capability.name} />
        ))}
      </div>
    </div>
  );
}

function TesseractHelp() {
  return (
    <div className={styles.help}>
      <h3>Cài Tesseract để đọc trang scan</h3>
      <ol>
        <li>Cài Tesseract OCR cho hệ điều hành của bạn.</li>
        <li>
          Thêm dữ liệu tiếng Việt <code>vie.traineddata</code> vào thư mục <code>tessdata</code> của Tesseract.
        </li>
        <li>
          Nếu Tesseract không nằm trong PATH, đặt <code>HOLOCRON_TESSERACT_CMD</code> (file chạy) hoặc{" "}
          <code>HOLOCRON_TESSDATA_DIR</code> cho backend, rồi khởi động lại backend.
        </li>
      </ol>
      <p className={styles.muted}>
        Chưa cài thì trang PDF có lớp chữ vẫn đọc được; trang scan sẽ báo lỗi cho tới khi cài xong.
      </p>
    </div>
  );
}

function KeyPolicy() {
  return (
    <section className={styles.section} aria-labelledby="start-keys">
      <h2 id="start-keys">Khoá API và dữ liệu của bạn</h2>
      <p>
        Holocron không bao giờ hỏi, nhận hay lưu khoá API trong giao diện. Muốn dùng LLM, đặt khoá bằng biến môi trường
        của backend rồi khởi động lại backend; bản desktop sẽ dùng kho khoá của hệ điều hành.
      </p>
      <p className={styles.muted}>
        Không cấu hình LLM thì không dữ liệu nào rời máy. Khi bật LLM, văn bản đã tách từ tài liệu được gửi tới dịch vụ
        LLM đã cấu hình.
      </p>
    </section>
  );
}
