"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useId, useRef, useState } from "react";
import { useShell } from "@/components/shell/ShellContext";
import { Accordion, Button, Dialog, Field, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { createWithRetry } from "@/lib/create-subject";
import type { UiError } from "@/lib/errors";
import { writeRecentSubject } from "@/lib/recent-subject";
import { suggestSubjectCode } from "@/lib/subject-code";
import { subjectHref } from "@/lib/subject-route";
import { checkSubjectForm } from "@/lib/subjects";
import { browserStorage } from "@/lib/theme";
import type { ExamFormat } from "@/types/holocron";

import styles from "./AddSubjectDialog.module.css";

/** Hình thức thi gửi khi tạo môn: như form tạo môn hiện có (mặc định của backend khi bỏ trống cũng là `mixed`). */
const DEFAULT_EXAM_FORMAT: ExamFormat = "mixed";

/** Mẫu mã môn nói ngắn cho người mới (thay câu dài của bước kiểm form). */
const CODE_FORMAT_HINT = "Mã môn cần có dạng như VXL01: vài chữ cái rồi 2–4 chữ số.";

/** Câu lỗi khi tạo môn theo loại lỗi (không lộ mã HTTP; mã kỹ thuật nằm trong "Chi tiết kỹ thuật"). */
function createErrorText(error: UiError): string {
  switch (error.kind) {
    case "network":
    case "server":
      return "Chưa kết nối được Holocron nên chưa tạo được môn. Mở lại Holocron rồi bấm Tạo môn lần nữa.";
    case "not_found":
      return "Bản Holocron đang chạy chưa tạo được môn riêng. Cập nhật Holocron lên bản mới hơn rồi thử lại.";
    case "validation":
    case "bad_request":
      return `Tên môn hoặc mã môn chưa đúng. ${CODE_FORMAT_HINT}`;
    case "busy":
      return "Holocron đang bận ghi dữ liệu. Đợi vài giây rồi bấm Tạo môn lần nữa.";
    default:
      return "Chưa tạo được môn. Bấm Tạo môn để thử lại.";
  }
}

export interface AddSubjectDialogProps {
  open: boolean;
  onClose: () => void;
  /** Mã môn đã có, để mã gợi ý không trùng. */
  existingCodes: readonly string[];
}

/**
 * Hộp thoại Thêm môn (design-handoff/README.md màn 2): chỉ cần tên môn; mã môn tự tạo (bỏ dấu, chữ đầu mỗi từ + số)
 * và sửa được trong "Tuỳ chọn nâng cao". Enter tạo, Esc đóng (hộp thoại gốc bẫy focus và trả focus). Backend báo trùng
 * mã (409) thì tăng số và thử lại một lần; vẫn trùng thì báo dưới ô mã môn và mở "Tuỳ chọn nâng cao". Tạo xong mở tab
 * Tài liệu của môn mới.
 */
export function AddSubjectDialog({ open, onClose, existingCodes }: AddSubjectDialogProps) {
  const router = useRouter();
  const toast = useToast();
  const { reloadSubjects, selectSubject } = useShell();
  const ids = useId();
  const formId = `${ids}-form`;
  const codeId = `${ids}-code`;
  const advancedRef = useRef<HTMLDivElement>(null);
  const [name, setName] = useState("");
  // `null`: chưa sửa mã môn, dùng mã tự tạo từ tên môn.
  const [typedCode, setTypedCode] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [error, setError] = useState<UiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const autoCode = name.trim() === "" ? "" : suggestSubjectCode(name, existingCodes);
  const code = typedCode ?? autoCode;

  function revealCodeField(): void {
    const details = advancedRef.current?.querySelector("details");
    if (details !== null && details !== undefined) details.open = true;
    document.getElementById(codeId)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (submitting) return;
    setError(null);
    if (name.trim() === "") {
      setNameError("Nhập tên môn");
      setCodeError(undefined);
      return;
    }
    const check = checkSubjectForm({ subjectCode: code, displayName: name, examFormat: DEFAULT_EXAM_FORMAT });
    if (!check.ok) {
      setNameError(check.errors.name);
      setCodeError(check.errors.code === undefined ? undefined : CODE_FORMAT_HINT);
      if (check.errors.code !== undefined) revealCodeField();
      return;
    }
    setNameError(undefined);
    setCodeError(undefined);
    setSubmitting(true);
    const outcome = await createWithRetry(api.createSubject, check.payload, existingCodes);
    if (outcome.ok) {
      const { subject } = outcome;
      toast.show(`Đã tạo môn ${subject.display_name.trim() || subject.subject_code}`);
      reloadSubjects();
      selectSubject(subject.subject_code);
      writeRecentSubject(browserStorage(), subject.subject_code);
      router.push(subjectHref(subject.subject_code, "documents"));
      return;
    }
    setSubmitting(false);
    // Ô mã môn hiện mã của lần gửi cuối (có thể là mã đã tăng số).
    if (outcome.code !== check.payload.subject_code) setTypedCode(outcome.code);
    if (outcome.error.kind === "duplicate") {
      setCodeError("Mã môn này đã có. Đổi số cuối rồi bấm Tạo môn.");
      revealCodeField();
      return;
    }
    if (outcome.error.kind === "validation" || outcome.error.kind === "bad_request") revealCodeField();
    setError(outcome.error);
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Thêm môn"
      description="Chỉ cần tên môn. Tài liệu thêm ở bước sau."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" type="submit" form={formId} busy={submitting} busyLabel="Đang tạo môn">
            Tạo môn
          </Button>
        </>
      }
    >
      <form id={formId} className={styles.form} onSubmit={(event) => void submit(event)} noValidate>
        <Field
          label="Tên môn"
          hint="Ví dụ: Vi xử lý"
          error={nameError}
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            if (nameError !== undefined) setNameError(undefined);
          }}
          autoComplete="off"
          required
        />
        <div ref={advancedRef}>
          <Accordion summary="Tuỳ chọn nâng cao">
            <Field
              id={codeId}
              label="Mã môn"
              hint="Tự tạo từ tên môn. Chỉ sửa nếu trường bạn dùng mã khác."
              error={codeError}
              value={code}
              onChange={(event) => {
                setTypedCode(event.target.value.trim().toUpperCase());
                if (codeError !== undefined) setCodeError(undefined);
              }}
              autoComplete="off"
              spellCheck={false}
            />
          </Accordion>
        </div>
        {error !== null && (
          <div className={styles.error} role="alert">
            <p>{createErrorText(error)}</p>
            {error.diagnostic !== undefined && (
              <Accordion summary="Chi tiết kỹ thuật">
                <code className={styles.code}>{error.diagnostic}</code>
              </Accordion>
            )}
          </div>
        )}
      </form>
    </Dialog>
  );
}
