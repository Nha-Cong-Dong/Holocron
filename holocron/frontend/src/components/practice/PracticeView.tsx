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
import type { PairsLocation } from "@/lib/pairs";
import type { PracticeBatch, PracticeBlueprint, PracticeTicket } from "@/lib/practice-data";
import { failureTechnical, ticketTechnical, ticketText } from "@/lib/practice-setup";
import { freshRuntime } from "@/lib/runtime";
import type { PracticeSessionKind } from "@/lib/subject-route";
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
  /** Môn của trang môn. */
  subjectCode: string;
  /**
   * Dựng địa chỉ phần khái niệm (nút "Duyệt khái niệm", liên kết khi câu lý thuyết không soạn được): tab Khái niệm
   * của môn.
   */
  hrefFor: (location: Partial<PairsLocation>) => string;
  /** `&session=` trên địa chỉ. Đang làm đề mà địa chỉ bỏ `session` (ví dụ bấm tên môn ở breadcrumb) ⇒ về thiết lập. */
  urlSession?: PracticeSessionKind | null;
  /** Báo phần đang mở: `quiz` (làm đề), `result` (kết quả) hoặc `null` (thiết lập, đang soạn, lỗi). */
  onSessionChange?: (session: PracticeSessionKind | null) => void;
}

/**
 * Tab Ôn tập (design-handoff/README.md màn 8–10; F4, lát cắt dọc §4): thiết lập đề (`PracticeSetup`) → tạo đề
 * (`POST /v1/blueprints`) → soạn câu (`POST /v1/blueprints/{id}/twins`) → làm từng câu → kết quả. State ở đây là nguồn
 * chân lý của bài làm; trang môn chỉ phản chiếu phần đang mở lên địa chỉ. Mọi con số và đáp án do backend trả; giao
 * diện chỉ so khoá.
 */
export function PracticeView({ subjectCode, hrefFor, urlSession = null, onSessionChange }: PracticeViewProps) {
  const router = useRouter();
  const { runtime, subjects } = useShell();
  const subject = selectedSubject(subjects, subjectCode);
  const [stage, setStage] = useState<Stage>({ kind: "setup" });
  const [phase, setPhase] = useState<PracticeSessionKind>("quiz");
  const session: PracticeSessionKind | null = stage.kind === "ready" && stage.batch.questions.length > 0 ? phase : null;

  // Địa chỉ vừa bỏ `session` trong khi đang làm đề hoặc xem kết quả: người dùng rời bài làm (breadcrumb, hộp lệnh).
  const [seenUrlSession, setSeenUrlSession] = useState(urlSession);
  if (seenUrlSession !== urlSession) {
    setSeenUrlSession(urlSession);
    if (seenUrlSession !== null && urlSession === null && session !== null) {
      setStage({ kind: "setup" });
      setPhase("quiz");
    }
  }

  useEffect(() => {
    onSessionChange?.(session);
  }, [session, onSessionChange]);
  const calls = useRef(createLatestCounter());
  // Một khoá cho mỗi bộ đề: "Thử lại" sau lỗi mạng gửi lại cùng body và nhận đúng lượt sinh cũ nếu backend đã sinh xong.
  const idempotencyKeys = useRef(new Map<string, string>());

  const generate = useCallback((blueprint: PracticeBlueprint) => {
    const ticket = calls.current.begin();
    setStage({ kind: "generating", blueprint });
    const key = idempotencyKeys.current.get(blueprint.blueprintId) ?? crypto.randomUUID();
    idempotencyKeys.current.set(blueprint.blueprintId, key);
    api
      .generatePracticeBatch(blueprint.blueprintId, { count: TWINS_PER_SLOT }, key)
      .then((batch) => {
        if (!calls.current.isLatest(ticket)) return;
        setPhase("quiz");
        setStage({ kind: "ready", blueprint, batch });
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
    setPhase("quiz");
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
  // Trang môn tự báo việc thiếu danh sách môn (mọi loại lỗi, kèm "Thử lại" khi hợp lý); không lặp lại.
  if (subjects.kind === "error") return null;
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
        <div className={styles.page} aria-busy="true">
          <output className={styles.busy}>
            <HolocronMark size={28} spinning />
            Đang soạn đề…
          </output>
          <Skeleton width="60%" height={28} />
          <Skeleton height={56} />
          <Skeleton height={56} />
        </div>
      )}
      {(stage.kind === "create_failed" || stage.kind === "generate_failed") && (
        <div className={styles.page}>
          {/* Lỗi tạo đề hay soạn câu (kể cả 409 khi đề nguồn chưa duyệt): chỉ hiện lý do, không hiện câu nào. */}
          <ErrorState
            title="Chưa soạn được đề"
            fix={screenErrorText(stage.error)}
            onRetry={() => (stage.kind === "create_failed" ? create(stage.payload) : generate(stage.blueprint))}
            technical={failureTechnical(stage.error)}
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
          onPhaseChange={setPhase}
          conceptsHref={hrefFor({})}
        />
      )}
    </div>
  );
}

interface ReadyBatchProps {
  batch: PracticeBatch;
  blueprint: PracticeBlueprint;
  /** "Thoát" khi đang làm, "Làm đề mới" ở Kết quả, "Soạn đề mới" khi đề không có câu: về thiết lập đề. */
  onNewExam: () => void;
  /** Bài làm chuyển giữa làm đề và kết quả. */
  onPhaseChange: (phase: PracticeSessionKind) => void;
  /** Địa chỉ phần khái niệm của môn sinh ra đề. */
  conceptsHref: string;
}

function ReadyBatch({ batch, blueprint, onNewExam, onPhaseChange, conceptsHref }: ReadyBatchProps) {
  const hasSkipped = batch.skipped.malformed > 0 || batch.skipped.unsupported > 0;
  if (batch.questions.length === 0) {
    return (
      <div className={styles.page}>
        <EmptyState
          title="Đề này không còn câu nào"
          description={
            batch.tickets.length > 0
              ? "Xem từng câu bên dưới, nhận thêm khái niệm ở chương đó rồi soạn đề mới, hoặc chọn chương khác."
              : "Quay lại để soạn đề mới với chương khác, hoặc nhận thêm khái niệm rồi soạn lại."
          }
          action={{ label: "Soạn đề mới", onClick: onNewExam }}
        />
        <TicketNotice tickets={batch.tickets} blueprint={blueprint} conceptsHref={conceptsHref} />
        {hasSkipped && <SkippedNotice />}
      </div>
    );
  }
  return (
    <div className={styles.page}>
      {/* Đoạn nguồn của trích dẫn tìm theo môn của blueprint sinh ra câu, không theo môn đang chọn (ADR-006). */}
      <PracticeSession
        questions={batch.questions}
        blueprint={blueprint}
        subjectCode={blueprint.subjectCode}
        onExit={onNewExam}
        onPhaseChange={onPhaseChange}
      />
      <TicketNotice tickets={batch.tickets} blueprint={blueprint} conceptsHref={conceptsHref} />
      {hasSkipped && <SkippedNotice />}
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
