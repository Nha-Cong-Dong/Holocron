"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";

import { Tabs } from "@/components/common/Tabs";
import {
  Accordion,
  Button,
  Card,
  Checkbox,
  Chip,
  Dialog,
  Dropdown,
  EmptyState,
  ErrorState,
  Field,
  Progress,
  Skeleton,
  Switch,
  Tooltip,
  useToast,
} from "@/components/ui";

import styles from "./UiGallery.module.css";

const DOCUMENT_TYPES = [
  { value: "slides", label: "Slide bài giảng" },
  { value: "textbook", label: "Giáo trình" },
  { value: "notes", label: "Ghi chép" },
  { value: "outline", label: "Đề cương" },
] as const;

type DocumentType = (typeof DOCUMENT_TYPES)[number]["value"];

const CHAPTERS = ["Chương 1", "Chương 2", "Chương 3"] as const;
const QUESTION_COUNTS = ["5", "10", "20"] as const;
const DEMO_TABS = [
  { id: "documents", label: "Tài liệu", icon: "document" },
  { id: "concepts", label: "Khái niệm", icon: "concept" },
  { id: "practice", label: "Ôn tập", icon: "practice" },
] as const;
type DemoTab = (typeof DEMO_TABS)[number]["id"];

/** Các mốc tiến độ mẫu (số cố định của trang dev, không tính ra). */
const PROGRESS_STEPS = [0, 12, 28, 40] as const;

/** Thời gian nút ở trạng thái đang xử lý khi bấm thử. */
const BUSY_DEMO_MS = 1600;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.section} aria-label={title}>
      <h2 className={styles.heading}>{title}</h2>
      <div className={styles.row}>{children}</div>
    </section>
  );
}

function CloseIcon() {
  return (
    <svg className={styles.icon} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

/**
 * Trang dev: mọi thành phần dùng chung ở mọi trạng thái, để xem animation đã chọn ở sáng/tối và khi giảm chuyển động.
 * Chỉ có route khi bật dữ liệu mẫu (`app/dev/ui/page.dev.tsx`, `pageExtensions` trong `next.config.ts`).
 */
export function UiGallery() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const busyTimer = useRef<number | null>(null);
  const [chapters, setChapters] = useState<readonly string[]>(["Chương 2"]);
  const [count, setCount] = useState<string>("10");
  const [autoAccept, setAutoAccept] = useState(true);
  const [ocrCheck, setOcrCheck] = useState(false);
  const [naturalQuestions, setNaturalQuestions] = useState(false);
  const [subjectName, setSubjectName] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("slides");
  const [tab, setTab] = useState<DemoTab>("documents");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [step, setStep] = useState(1);

  useEffect(() => {
    return () => {
      if (busyTimer.current !== null) window.clearTimeout(busyTimer.current);
    };
  }, []);

  function runBusy(): void {
    setBusy(true);
    busyTimer.current = window.setTimeout(() => {
      busyTimer.current = null;
      setBusy(false);
    }, BUSY_DEMO_MS);
  }

  function toggleChapter(chapter: string, next: boolean): void {
    setChapters((current) => (next ? [...current, chapter] : current.filter((item) => item !== chapter)));
  }

  const progressValue = PROGRESS_STEPS[step] ?? 0;
  const progressMax = PROGRESS_STEPS[PROGRESS_STEPS.length - 1] ?? 0;

  return (
    <div className={styles.page}>
      <p className={styles.intro}>
        Trang dành cho người phát triển: mọi thành phần dùng chung ở mọi trạng thái. Đổi sáng/tối bằng nút trên thanh
        trên, bật “Giảm chuyển động” trong Cài đặt để xem bản tĩnh.
      </p>

      <Section title="Nút">
        <Button variant="primary" busy={busy} busyLabel="Đang soạn đề" onClick={runBusy}>
          Bắt đầu
        </Button>
        <Button variant="primary" disabled>
          Bắt đầu đọc (0 file)
        </Button>
        <Button variant="secondary">Sửa</Button>
        <Button variant="secondary" arrow>
          Ôn tiếp
        </Button>
        <Button variant="secondary" busy busyLabel="Đang kiểm tra">
          Kiểm tra lại
        </Button>
        <Button variant="icon" aria-label="Đóng">
          <CloseIcon />
        </Button>
      </Section>

      <Section title="Chip chọn">
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Chương (chọn nhiều)</legend>
          {CHAPTERS.map((chapter) => (
            <Chip
              key={chapter}
              kind="toggle"
              pressed={chapters.includes(chapter)}
              onToggle={(next) => toggleChapter(chapter, next)}
            >
              {chapter}
            </Chip>
          ))}
        </fieldset>
        <fieldset className={styles.group}>
          <legend className={styles.legend}>Số câu (chọn một)</legend>
          {QUESTION_COUNTS.map((option) => (
            <Chip
              key={option}
              kind="radio"
              name="gallery-count"
              value={option}
              checked={count === option}
              onSelect={setCount}
            >
              {option} câu
            </Chip>
          ))}
          <Chip kind="radio" name="gallery-count-disabled" value="mixed" checked={false} onSelect={() => {}} disabled>
            Kết hợp
          </Chip>
        </fieldset>
      </Section>

      <Section title="Ô đánh dấu">
        <Checkbox checked={autoAccept} onChange={setAutoAccept}>
          Tự nhận trang rõ
        </Checkbox>
        <Checkbox checked={ocrCheck} onChange={setOcrCheck} hint="Chậm hơn, nhưng bắt được chữ số đọc sai.">
          Đọc lại số hai lần
        </Checkbox>
        <Checkbox checked onChange={() => {}} disabled>
          Không đổi được
        </Checkbox>
      </Section>

      <Section title="Công tắc">
        <div className={styles.switchRow}>
          <span id="gallery-switch-label">Câu hỏi tự nhiên hơn</span>
          <Switch checked={naturalQuestions} onChange={setNaturalQuestions} labelledBy="gallery-switch-label" />
        </div>
        <div className={styles.switchRow}>
          <span>Đang bật, không đổi được</span>
          <Switch checked onChange={() => {}} label="Công tắc mẫu đang bật" disabled />
        </div>
      </Section>

      <Section title="Ô nhập">
        <Field label="Tên môn" value={subjectName} onChange={(event) => setSubjectName(event.target.value)} />
        <Field label="Mã môn" hint="Bỏ trống để Holocron tự đặt." defaultValue="VXL01" />
        <Field label="Chương" defaultValue="Chương mười" error="Chương cần là số, ví dụ 2." />
      </Section>

      <Section title="Bảng thả xuống">
        <Dropdown label="Loại tài liệu" options={DOCUMENT_TYPES} value={documentType} onChange={setDocumentType} />
      </Section>

      <section className={styles.section} aria-label="Tab">
        <h2 className={styles.heading}>Tab</h2>
        <Tabs label="Tab mẫu" items={DEMO_TABS} selected={tab} onSelect={setTab} idPrefix="gallery-tabs">
          <p className={styles.pane}>
            {tab === "documents" && "3 file · 96 trang"}
            {tab === "concepts" && "6 đề xuất đang chờ"}
            {tab === "practice" && "Chọn chòm sao để ôn"}
          </p>
        </Tabs>
      </section>

      <Section title="Gợi ý nổi">
        <Tooltip content="Mỗi sao là một khái niệm đã nhận">
          {(trigger) => (
            <Button variant="icon" aria-label="Sao trên bầu trời là gì?" {...trigger}>
              <span aria-hidden="true">?</span>
            </Button>
          )}
        </Tooltip>
      </Section>

      <Section title="Thẻ">
        <Card className={styles.card}>
          <strong>Vi xử lý</strong>
          <span className={styles.muted}>3 chòm sao · 34 khái niệm</span>
          <Button variant="secondary" arrow>
            Ôn tiếp
          </Button>
        </Card>
        <Card className={styles.card} tilt={false}>
          <strong>Thẻ không nghiêng</strong>
          <span className={styles.muted}>Dùng khi trong thẻ có chữ để bôi chọn.</span>
        </Card>
      </Section>

      <Section title="Thông báo nhanh">
        <Button variant="secondary" onClick={() => toast.show("Đã nhận 6 khái niệm")}>
          Hiện thông báo
        </Button>
        <Button variant="secondary" onClick={() => toast.show("Đã đọc xong 3 file")}>
          Hiện thông báo khác
        </Button>
      </Section>

      <Section title="Hộp thoại">
        <Button variant="secondary" onClick={() => setDialogOpen(true)}>
          Mở hộp thoại
        </Button>
        <Dialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title="Thêm môn"
          description="Chỉ cần tên môn."
          footer={
            <>
              <Button variant="secondary" onClick={() => setDialogOpen(false)}>
                Huỷ
              </Button>
              <Button variant="primary" onClick={() => setDialogOpen(false)}>
                Tạo môn
              </Button>
            </>
          }
        >
          <Field label="Tên môn" />
          <Accordion summary="Tuỳ chọn nâng cao">
            <Field label="Mã môn" hint="Bỏ trống để Holocron tự đặt." />
          </Accordion>
        </Dialog>
      </Section>

      <Section title="Mục thu gọn">
        <Accordion summary="Chi tiết kỹ thuật" className={styles.accordion}>
          <p>Độ tin cậy: 94,2%</p>
          <p>Số đoạn: 212</p>
          <p>Bộ đọc: lớp chữ PDF</p>
        </Accordion>
      </Section>

      <Section title="Thanh tiến độ">
        <div className={styles.progress}>
          <Progress value={progressValue} max={progressMax} unit="trang" label="Tiến độ đọc file mẫu" active showText />
          <Button variant="secondary" onClick={() => setStep((current) => (current + 1) % PROGRESS_STEPS.length)}>
            Bước tiếp
          </Button>
        </div>
        <div className={styles.progress}>
          <Progress value={0} max={10} unit="câu" label="Tiến độ làm bài mẫu" showText />
          <Progress value={10} max={10} unit="câu" label="Bài mẫu đã xong" showText />
        </div>
      </Section>

      <Section title="Đang tải">
        <div className={styles.skeleton}>
          <Skeleton circle width={44} height={44} />
          <div className={styles.skeletonLines}>
            <Skeleton width="60%" height={20} />
            <Skeleton />
            <Skeleton width="80%" />
          </div>
        </div>
      </Section>

      <Section title="Trạng thái rỗng">
        <EmptyState
          className={styles.wide}
          title="Chưa có tài liệu nào"
          description="Thêm slide hoặc giáo trình của môn để bắt đầu."
          action={{ label: "Thêm tài liệu", onClick: () => toast.show("Mở hộp thêm tài liệu") }}
        />
      </Section>

      <Section title="Trạng thái lỗi">
        <ErrorState
          className={styles.wide}
          title="Không đọc được “Bài giảng ngắt.pdf”."
          fix="File đang đặt mật khẩu nên chưa mở được. Bỏ mật khẩu rồi bấm Thử lại, hoặc xoá file này khỏi danh sách."
          technical="pdf_encrypted · trang 1/40"
          onRetry={() => toast.show("Đang thử lại")}
        />
      </Section>
    </div>
  );
}
