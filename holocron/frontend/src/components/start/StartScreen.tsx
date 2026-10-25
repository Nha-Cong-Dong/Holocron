"use client";

import Link from "next/link";

import { CapabilityNoticeBox } from "@/components/common/CapabilityNotice";
import { ErrorNotice } from "@/components/common/ErrorNotice";
import { Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { IS_DEVELOPMENT } from "@/lib/config";
import {
  type CapabilityEntry,
  capabilitiesOf,
  capabilityLabel,
  type DataEgress,
  freshRuntime,
  groupCapabilities,
  needsTesseractHelp,
  type RuntimeView,
  selectDataEgress,
  type UnavailableGroup as UnavailableGroupName,
} from "@/lib/runtime";

import styles from "./StartScreen.module.css";

const GROUP_TEXT: Record<UnavailableGroupName, { title: string; intro: string }> = {
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
  unrecognized: {
    title: "Giao diện chưa nhận ra",
    intro:
      "Backend báo các khả năng hoặc trạng thái dưới đây nhưng giao diện này chưa biết chúng. Coi như chúng có thể gửi dữ liệu ra khỏi máy; cập nhật giao diện để xem chi tiết.",
  },
};

const EGRESS_TEXT: Record<DataEgress, string> = {
  unknown:
    "Chưa đọc được trạng thái LLM và VLM của backend, nên chưa biết lúc này có dữ liệu nào có thể rời máy hay không.",
  local_only: "Hiện chưa bật LLM hay VLM: không dữ liệu nào rời máy.",
  may_leave:
    "Hiện đã bật LLM hoặc VLM: khi bạn dùng các tính năng ở nhóm “có gửi dữ liệu ra khỏi máy”, dữ liệu tương ứng được gửi tới dịch vụ đã cấu hình.",
};

/** Màn chạy lần đầu (trang gốc): đọc `capabilities` để nói rõ cái gì chạy offline được, cái gì cần cài thêm. */
export function StartScreen() {
  const { runtime, refreshRuntime } = useShell();
  // Chỉ hiện khả năng khi lần tải gần nhất thành công; dữ liệu cũ không được trình bày như trạng thái hiện tại.
  const current = freshRuntime(runtime);

  return (
    <div className={styles.screen}>
      <section className={styles.intro}>
        <h2>Chào mừng tới Holocron</h2>
        <p>
          Holocron đọc tài liệu môn học của bạn, dựng kho tri thức có trích dẫn nguyên văn và sinh câu hỏi luyện tập có
          kiểm chứng. Mọi con số trong câu hỏi do backend tính; giao diện chỉ hiển thị.
        </p>
      </section>

      {runtime.backend === "connecting" && (
        <Loading
          label={
            runtime.runtime === null
              ? "Đang kiểm tra backend và khả năng của máy này…"
              : "Đang kiểm tra lại backend và khả năng của máy này…"
          }
        />
      )}

      {runtime.error !== null && (
        <section className={styles.section} aria-labelledby="start-backend">
          <h2 id="start-backend">
            {runtime.backend === "unreachable" ? "Chưa kết nối được backend" : "Backend từ chối yêu cầu"}
          </h2>
          <ErrorNotice error={runtime.error} onAction={refreshRuntime} />
          {runtime.runtime !== null && (
            <p className={styles.muted}>
              Danh sách khả năng của lần kiểm tra trước đã được ẩn vì chưa cập nhật được; nó sẽ hiện lại khi backend trả
              lời.
            </p>
          )}
          {runtime.backend === "unreachable" && <BackendHelp />}
        </section>
      )}

      {current !== null && <CapabilityOverview runtime={current} />}

      <KeyPolicy egress={selectDataEgress(current)} />

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

function CapabilityOverview({ runtime }: { runtime: RuntimeView }) {
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
        title="Đang dùng được, có gửi dữ liệu ra khỏi máy"
        capabilities={groups.online}
        badge="dùng mạng"
        tone="info"
        note="Khi dùng các tính năng này, văn bản đã tách từ tài liệu (với LLM) hoặc ảnh trang (với VLM) được gửi tới dịch vụ đã cấu hình."
      />
      <UnavailableGroup group="install" capabilities={groups.install} />
      {needsTesseractHelp(runtime) && <TesseractHelp />}
      <UnavailableGroup group="configure" capabilities={groups.configure} />
      <UnavailableGroup group="not_implemented" capabilities={groups.not_implemented} />
      <UnavailableGroup group="unrecognized" capabilities={groups.unrecognized} />
    </section>
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
      <h3>{title}</h3>
      {note !== null && <p className={styles.muted}>{note}</p>}
      <ul className={styles.ready}>
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
  group: UnavailableGroupName;
  capabilities: CapabilityEntry[];
}

function UnavailableGroup({ group, capabilities }: UnavailableGroupProps) {
  if (capabilities.length === 0) return null;
  const text = GROUP_TEXT[group];
  return (
    <div className={styles.group}>
      <h3>{text.title}</h3>
      <p className={styles.muted}>{text.intro}</p>
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

function KeyPolicy({ egress }: { egress: DataEgress }) {
  return (
    <section className={styles.section} aria-labelledby="start-keys">
      <h2 id="start-keys">Khoá API và dữ liệu của bạn</h2>
      <p>
        Holocron không bao giờ hỏi, nhận hay lưu khoá API trong giao diện. Muốn dùng LLM, đặt khoá bằng biến môi trường
        của backend rồi khởi động lại backend; bản desktop sẽ dùng kho khoá của hệ điều hành.
      </p>
      <p className={styles.muted}>
        Dữ liệu chỉ rời máy khi bật LLM hoặc VLM: văn bản đã tách từ tài liệu được gửi tới LLM đã cấu hình, ảnh trang
        được gửi tới VLM đã cấu hình. Không bật cả hai thì không dữ liệu nào rời máy.
      </p>
      <p className={styles.muted}>{EGRESS_TEXT[egress]}</p>
    </section>
  );
}
