"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState, Loading } from "@/components/common/LoadState";
import { useShell } from "@/components/shell/ShellContext";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { createLatestCounter } from "@/lib/latest";
import { type PairsLocation, pairsHref } from "@/lib/pairs";
import { ticketLine } from "@/lib/practice";
import type { PracticeBatch, PracticeBlueprint, PracticeTicket } from "@/lib/practice-data";
import { freshRuntime } from "@/lib/runtime";
import { selectedSubject } from "@/lib/subjects";
import type { BlueprintCreate } from "@/types/holocron";

import { BlueprintBuilder } from "./BlueprintBuilder";
import styles from "./Practice.module.css";
import { PracticeSession } from "./PracticeSession";

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
  /** Dựng địa chỉ phần khái niệm (liên kết khi câu lý thuyết không sinh được); mặc định `pairsHref` (`/pairs?…`). */
  hrefFor?: (location: Partial<PairsLocation>) => string;
}

/**
 * Màn luyện tập (F4, lát cắt dọc §4): chọn môn (trang môn truyền qua prop; route cũ `/practice` dùng môn của khung) →
 * tạo đề (`POST /v1/blueprints`) → sinh câu (`POST /v1/blueprints/{id}/twins`) → làm từng câu. Mọi con số và đáp án do
 * backend trả; giao diện chỉ so khoá.
 */
export function PracticeView({ subjectCode: fixedSubject, hrefFor = pairsHref }: PracticeViewProps) {
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

  if (subjects.kind === "loading") return <Loading label="Đang tải danh sách môn…" />;
  if (subjects.kind === "error") return <SubjectsUnavailable error={subjects.error} onRetry={reloadSubjects} />;
  if (subjects.subjects.length === 0) {
    return (
      <EmptyState title="Chưa có môn học nào.">
        Tạo môn và nhập tài liệu ở mục <Link href="/vault">Vault</Link>. Sau khi xác nhận cặp khái niệm trong Vault,
        quay lại đây để luyện tập.
      </EmptyState>
    );
  }
  if (subject === null) {
    return (
      <EmptyState title="Chưa chọn môn.">
        Mở một môn ở <Link href="/">trang chủ</Link> để tạo đề luyện tập cho môn đó.
      </EmptyState>
    );
  }

  return (
    <div className={styles.page}>
      {(stage.kind === "setup" || stage.kind === "creating" || stage.kind === "create_failed") && (
        <>
          <BlueprintBuilder
            key={subject.subject_code}
            subject={subject}
            runtime={freshRuntime(runtime)}
            busy={stage.kind === "creating"}
            onCreate={create}
          />
          {stage.kind === "create_failed" && <ErrorNotice error={stage.error} onRetry={() => create(stage.payload)} />}
        </>
      )}
      {stage.kind === "generating" && <Loading label="Đang sinh câu hỏi từ các cặp khái niệm đã xác nhận…" />}
      {stage.kind === "generate_failed" && (
        <div className={styles.page}>
          {/* Lỗi sinh câu (kể cả 409 khi đề nguồn chưa duyệt): chỉ hiện lý do, không hiện câu nào. */}
          <ErrorNotice error={stage.error} onRetry={() => generate(stage.blueprint)} />
          <div className={styles.actions}>
            <button type="button" className={styles.secondary} onClick={restart}>
              Tạo đề khác
            </button>
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

function SubjectsUnavailable({ error, onRetry }: { error: UiError; onRetry: () => void }) {
  if (error.kind === "not_found") {
    return (
      <EmptyState title="Backend đang chạy chưa có danh sách môn học.">
        Màn luyện tập cần biết môn và hình thức thi của môn. Cập nhật backend lên bản có danh sách môn, hoặc trong lúc
        chờ hãy <Link href="/ingestion">nhập đề thi</Link>.
      </EmptyState>
    );
  }
  return <ErrorNotice error={error} onRetry={onRetry} onReload={onRetry} />;
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
        <EmptyState title="Lượt sinh này không có câu nào để luyện.">
          {batch.tickets.length > 0
            ? "Lý do của từng câu do backend ghi ở bên dưới. Sửa theo gợi ý rồi tạo lại đề, hoặc tạo đề với chương khác."
            : "Thử tạo đề với chương khác."}
        </EmptyState>
        <TicketNotice tickets={batch.tickets} conceptsHref={conceptsHref} />
        {hasSkipped && <SkippedNotice />}
        <div className={styles.actions}>
          <button type="button" className={styles.primary} onClick={onNewExam}>
            Tạo đề khác
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className={styles.page}>
      <TicketNotice tickets={batch.tickets} conceptsHref={conceptsHref} />
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

/**
 * Slot không sinh được (`review_tickets`): hiện nguyên lý do backend ghi, kèm gợi ý cố định với slot lý thuyết
 * (`ticketLine`); không hiện câu hỏng.
 */
function TicketNotice({ tickets, conceptsHref }: { tickets: PracticeTicket[]; conceptsHref: string }) {
  if (tickets.length === 0) return null;
  return (
    <section className={styles.tickets} aria-labelledby="practice-tickets-title">
      <h2 id="practice-tickets-title" className={styles.sectionTitle}>
        Một số câu trong đề không sinh được
      </h2>
      <ul>
        {tickets.map((ticket) => (
          <li key={ticket.slotIndex}>
            Câu thứ {ticket.slotIndex} trong đề: {ticketLine(ticket)}
          </li>
        ))}
      </ul>
      {tickets.some((ticket) => ticket.theory) && (
        <p>
          <Link href={conceptsHref}>Mở trang Cặp khái niệm</Link>
        </p>
      )}
    </section>
  );
}

function SkippedNotice() {
  return (
    <p className={styles.note}>
      Có câu bị bỏ qua vì dữ liệu backend trả về không đúng dạng giao diện này hiểu, hoặc là dạng câu màn luyện tập chưa
      hỗ trợ (ví dụ câu điền số). Các câu còn lại vẫn làm được bình thường.
    </p>
  );
}
