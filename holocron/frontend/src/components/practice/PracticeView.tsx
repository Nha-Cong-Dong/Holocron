"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { useShell } from "@/components/shell/ShellContext";
import { Accordion, Button, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { api, toUiError } from "@/lib/api";
import { screenErrorText } from "@/lib/concept-board";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { type PairsLocation, pairsHref } from "@/lib/pairs";
import type { PracticeBatch, PracticeBlueprint, PracticeTicket } from "@/lib/practice-data";
import { ticketTechnical, ticketText } from "@/lib/practice-setup";
import { freshRuntime } from "@/lib/runtime";
import { selectedSubject } from "@/lib/subjects";
import type { BlueprintCreate } from "@/types/holocron";

import styles from "./Practice.module.css";
import { PracticeSession } from "./PracticeSession";
import { PracticeSetup } from "./PracticeSetup";

/** Mỗi slot của đề luyện tập sinh một câu (một lượt làm bài đi qua từng slot một lần). */
const TWINS_PER_SLOT = 1;

type Stage =
  | { kind: "setup" }
  | { kind: "creating" }
  | { kind: "create_failed"; error: UiError; payload: BlueprintCreate }
  | { kind: "generating"; blueprint: PracticeBlueprint }
  | { kind: "generate_failed"; error: UiError; blueprint: PracticeBlueprint }
  | { kind: "ready"; blueprint: PracticeBlueprint; batch: PracticeBatch };

export interface PracticeViewProps {
  /** Môn cố định (trang môn); ưu tiên hơn môn của khung. Bỏ trống ⇒ môn của khung (route cũ `/practice`). */
  subjectCode?: string;
  /**
   * Dựng địa chỉ phần khái niệm (nút "Duyệt khái niệm", liên kết khi câu lý thuyết không soạn được); trang môn truyền
   * địa chỉ tab Khái niệm của môn, mặc định `pairsHref` (`/pairs?…`, route cũ).
   */
  hrefFor?: (location: Partial<PairsLocation>) => string;
}

/**
 * Tab Ôn tập (design-handoff/README.md màn 8; F4, lát cắt dọc §4): thiết lập đề (`PracticeSetup`; trang môn truyền môn
 * qua prop, route cũ `/practice` dùng môn của khung) → tạo đề (`POST /v1/blueprints`) → soạn câu
 * (`POST /v1/blueprints/{id}/twins`) → làm từng câu. Mọi con số và đáp án do backend trả; giao diện chỉ so khoá.
 */
export function PracticeView({ subjectCode: fixedSubject, hrefFor = pairsHref }: PracticeViewProps) {
  const router = useRouter();
  const { runtime, subjects, reloadSubjects, subjectCode: shellSubject } = useShell();
  const subjectCode = fixedSubject ?? shellSubject;
  const subject = selectedSubject(subjects, subjectCode);
  const [stage, setStage] = useState<Stage>({ kind: "setup" });
  const calls = useRef(createLatestCounter());
  // Một khoá cho mỗi bộ đề: "Thử lại" sau lỗi mạng gửi lại cùng body và nhận đúng lượt sinh cũ nếu backend đã sinh xong.
  const idempotencyKeys = useRef(new Map<string, string>());

  // Đổi môn: bỏ đề đang làm của môn cũ và mọi lời gọi còn chờ.
  useEffect(() => {
    if (subjectCode === null) return;
    calls.current.invalidate();
    setStage({ kind: "setup" });
  }, [subjectCode]);

  const generate = useCallback((blueprint: PracticeBlueprint) => {
    const ticket = calls.current.begin();
    setStage({ kind: "generating", blueprint });
    const key = idempotencyKeys.current.get(blueprint.blueprintId) ?? crypto.randomUUID();
    idempotencyKeys.current.set(blueprint.blueprintId, key);
    api
      .generatePracticeBatch(blueprint.blueprintId, { count: TWINS_PER_SLOT }, key)
      .then((batch) => {
        if (calls.current.isLatest(ticket)) setStage({ kind: "ready", blueprint, batch });
      })
      .catch((error: unknown) => {
        if (calls.current.isLatest(ticket)) setStage({ kind: "generate_failed", error: toUiError(error), blueprint });
      });
  }, []);

  const create = useCallback(
    (payload: BlueprintCreate) => {
      const ticket = calls.current.begin();
      setStage({ kind: "creating" });
      api
        .createBlueprint(payload)
        .then((blueprint) => {
          if (calls.current.isLatest(ticket)) generate(blueprint);
        })
        .catch((error: unknown) => {
          if (calls.current.isLatest(ticket)) setStage({ kind: "create_failed", error: toUiError(error), payload });
        });
    },
    [generate],
  );

  const restart = useCallback(() => {
    calls.current.invalidate();
    setStage({ kind: "setup" });
  }, []);

  if (subjects.kind === "loading") {
    return (
      <div className={styles.page} aria-busy="true">
        <Skeleton width="30%" height={22} />
        <Skeleton height={120} />
        <p className={styles.muted}>Đang tải danh sách môn…</p>
      </div>
    );
  }
  if (subjects.kind === "error") {
    // Trong trang môn, trang tự báo việc thiếu danh sách môn (mọi loại lỗi, kèm "Thử lại" khi hợp lý); không lặp lại.
    if (fixedSubject !== undefined) return null;
    return <SubjectsUnavailable error={subjects.error} onRetry={reloadSubjects} onHome={() => router.push("/")} />;
  }
  if (subjects.subjects.length === 0 || subject === null) {
    return (
      <EmptyState
        title={subjects.subjects.length === 0 ? "Chưa có môn học nào" : "Chưa chọn môn"}
        description="Mở hoặc tạo một môn ở trang chủ, nhận khái niệm ở tab Khái niệm, rồi ôn tập ở tab Ôn tập của môn."
        action={{ label: "Về trang chủ", onClick: () => router.push("/") }}
      />
    );
  }

  return (
    <div className={styles.page}>
      {stage.kind === "setup" && (
        <PracticeSetup
          key={subject.subject_code}
          subject={subject}
          runtime={freshRuntime(runtime)}
          onStart={create}
          onBrowseConcepts={() => router.push(hrefFor({}))}
        />
      )}
      {(stage.kind === "creating" || stage.kind === "generating") && (
        <output className={styles.busy}>
          <HolocronMark size={28} spinning />
          Đang soạn đề…
        </output>
      )}
      {(stage.kind === "create_failed" || stage.kind === "generate_failed") && (
        <div className={styles.page}>
          {/* Lỗi tạo đề hay soạn câu (kể cả 409 khi đề nguồn chưa duyệt): chỉ hiện lý do, không hiện câu nào. */}
          <ErrorState
            title="Chưa soạn được đề"
            fix={failureText(stage.error)}
            onRetry={() => (stage.kind === "create_failed" ? create(stage.payload) : generate(stage.blueprint))}
            technical={stage.error.diagnostic}
          />
          <div className={styles.actions}>
            <Button variant="secondary" onClick={restart}>
              Tạo đề khác
            </Button>
          </div>
        </div>
      )}
      {stage.kind === "ready" && (
        <ReadyBatch
          key={stage.batch.batchId ?? stage.blueprint.blueprintId}
          batch={stage.batch}
          blueprint={stage.blueprint}
          onNewExam={restart}
          conceptsHref={hrefFor({})}
        />
      )}
    </div>
  );
}

/** Vì sao và cách sửa khi chưa soạn được đề; lỗi chặn ở bước kiểm duyệt đề kèm danh sách lỗi do Gate viết. */
function failureText(error: UiError): string {
  const base = screenErrorText(error);
  return error.issues.length > 0 ? `${base} ${error.issues.join("; ")}` : base;
}

/** Route cũ `/practice` khi chưa có danh sách môn. */
function SubjectsUnavailable({ error, onRetry, onHome }: { error: UiError; onRetry: () => void; onHome: () => void }) {
  if (error.kind === "not_found") {
    return (
      <EmptyState
        title="Bản Holocron đang chạy chưa có danh sách môn học"
        description="Màn ôn tập cần biết môn và hình thức thi của môn. Cập nhật Holocron lên bản mới hơn rồi mở lại."
        action={{ label: "Về trang chủ", onClick: onHome }}
      />
    );
  }
  return (
    <ErrorState
      title="Không tải được danh sách môn"
      fix={screenErrorText(error)}
      onRetry={onRetry}
      technical={error.diagnostic}
    />
  );
}

interface ReadyBatchProps {
  batch: PracticeBatch;
  blueprint: PracticeBlueprint;
  onNewExam: () => void;
  /** Địa chỉ phần khái niệm của môn sinh ra đề. */
  conceptsHref: string;
}

function ReadyBatch({ batch, blueprint, onNewExam, conceptsHref }: ReadyBatchProps) {
  const hasSkipped = batch.skipped.malformed > 0 || batch.skipped.unsupported > 0;
  if (batch.questions.length === 0) {
    return (
      <div className={styles.page}>
        <EmptyState
          title="Lượt này chưa soạn được câu nào"
          description={
            batch.tickets.length > 0
              ? "Xem từng câu bên dưới, nhận thêm khái niệm ở chương đó rồi tạo đề lại, hoặc tạo đề với chương khác."
              : "Tạo đề với chương khác, hoặc nhận thêm khái niệm rồi tạo đề lại."
          }
          action={{ label: "Tạo đề khác", onClick: onNewExam }}
        />
        <TicketNotice tickets={batch.tickets} blueprint={blueprint} conceptsHref={conceptsHref} />
        {hasSkipped && <SkippedNotice />}
      </div>
    );
  }
  return (
    <div className={styles.page}>
      <TicketNotice tickets={batch.tickets} blueprint={blueprint} conceptsHref={conceptsHref} />
      {hasSkipped && <SkippedNotice />}
      {/* Đoạn nguồn của trích dẫn tìm theo môn của blueprint sinh ra câu, không theo môn đang chọn (ADR-006). */}
      <PracticeSession
        questions={batch.questions}
        blueprint={blueprint}
        subjectCode={blueprint.subjectCode}
        onNewExam={onNewExam}
      />
    </div>
  );
}

interface TicketNoticeProps {
  tickets: PracticeTicket[];
  blueprint: PracticeBlueprint;
  conceptsHref: string;
}

/**
 * Câu chưa soạn được (`review_tickets`): mỗi câu một câu chung kèm việc nên làm (`ticketText`, không so chuỗi lý do);
 * lý do nguyên văn chỉ nằm trong "Chi tiết kỹ thuật". Không hiện câu hỏng.
 */
function TicketNotice({ tickets, blueprint, conceptsHref }: TicketNoticeProps) {
  if (tickets.length === 0) return null;
  return (
    <section className={styles.tickets} aria-labelledby="practice-tickets-title">
      <h2 id="practice-tickets-title" className={styles.sectionTitle}>
        Một số câu chưa soạn được
      </h2>
      <ul>
        {tickets.map((ticket) => (
          <li key={ticket.slotIndex}>{ticketText(ticket, blueprint.slots)}</li>
        ))}
      </ul>
      {tickets.some((ticket) => ticket.theory) && (
        <p>
          <Link href={conceptsHref}>Mở tab Khái niệm</Link>
        </p>
      )}
      <Accordion summary="Chi tiết kỹ thuật">
        <pre className={styles.technical}>{ticketTechnical(tickets)}</pre>
      </Accordion>
    </section>
  );
}

function SkippedNotice() {
  return (
    <p className={styles.note}>
      Có câu bị bỏ qua vì Holocron trả về dữ liệu mà giao diện này chưa đọc được, hoặc là dạng câu màn ôn tập chưa hỗ
      trợ (ví dụ câu điền số). Các câu còn lại vẫn làm được bình thường.
    </p>
  );
}
