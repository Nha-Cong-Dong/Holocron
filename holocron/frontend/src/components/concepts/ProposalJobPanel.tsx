"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { canStartJob, LLM_NEEDED_VI, latestJob } from "@/lib/concept-proposals";
import { toUiError, type UiError } from "@/lib/errors";
import { isJobActive, jobHeadline, jobLines, type ProposalJobView } from "@/lib/import-result";
import { createPoller, type Poller } from "@/lib/poll";
import type { CapabilityView } from "@/lib/runtime";

import styles from "./ConceptsTab.module.css";

export interface ProposalJobDocument {
  docId: string;
  title: string;
}

interface ProposalJobPanelProps {
  subjectCode: string;
  /** Khả năng `concept_pair_llm_proposal` từ `GET /v1/runtime` (nguồn duy nhất cho trạng thái "không có LLM"). */
  capability: CapabilityView["state"];
  documents: readonly ProposalJobDocument[];
  /** Job vừa kết thúc: nơi gọi tải lại danh sách cặp để cặp mới hiện ra. */
  onFinished: () => void;
}

/**
 * Tiến độ job đề xuất cặp của môn (job mới nhất, hỏi lại `GET …/jobs` khi còn chạy) và lối vào tạo job. Không có LLM
 * (`capability = "unavailable"`) ⇒ ẩn nút tạo job, hiện một câu và để nguyên đường tự bôi. Mọi số do backend đếm.
 */
export function ProposalJobPanel({ subjectCode, capability, documents, onFinished }: ProposalJobPanelProps) {
  const [job, setJob] = useState<ProposalJobView | null>(null);
  const [pollError, setPollError] = useState<UiError | null>(null);
  const [startingDoc, setStartingDoc] = useState<string | null>(null);
  const [startError, setStartError] = useState<UiError | null>(null);
  const poller = useRef<Poller | null>(null);
  const wasActive = useRef(false);
  const finishedRef = useRef(onFinished);
  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  useEffect(() => {
    wasActive.current = false;
    const next = createPoller<ProposalJobView | null>({
      load: async () => latestJob(await api.listProposalJobs(subjectCode)),
      shouldContinue: (value) => value !== null && isJobActive(value.status),
      onValue: (value) => {
        setPollError(null);
        setJob(value);
        const active = value !== null && isJobActive(value.status);
        if (wasActive.current && !active) finishedRef.current();
        wasActive.current = active;
      },
      onError: (error, gaveUp) => {
        if (gaveUp) setPollError(toUiError(error));
      },
    });
    poller.current = next;
    next.start();
    return () => {
      next.stop();
      poller.current = null;
    };
  }, [subjectCode]);

  const restartPolling = useCallback(() => {
    setPollError(null);
    poller.current?.stop();
    poller.current?.start();
  }, []);

  async function start(doc: ProposalJobDocument): Promise<void> {
    if (startingDoc !== null) return;
    setStartingDoc(doc.docId);
    setStartError(null);
    try {
      const started = await api.startProposalJob(subjectCode, doc.docId);
      setJob(started);
      wasActive.current = isJobActive(started.status);
      restartPolling();
    } catch (caught) {
      setStartError(toUiError(caught));
    } finally {
      setStartingDoc(null);
    }
  }

  const showNeedLlm = capability === "unavailable";
  const showStart = canStartJob(capability, job) && documents.length > 0;
  if (!showNeedLlm && job === null && !showStart && pollError === null) return null;

  return (
    <section className={styles.jobPanel} aria-label="Đề xuất khái niệm tự động">
      {showNeedLlm && <p className={styles.muted}>{LLM_NEEDED_VI}</p>}
      <div aria-live="polite">
        {job !== null && (
          <>
            <p className={styles.progress}>{jobHeadline(job)}</p>
            <ul className={styles.jobList}>
              {jobLines(job).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </>
        )}
      </div>
      {pollError !== null && (
        <div className={styles.actions}>
          <p className={styles.error} role="alert">
            Chưa cập nhật được tiến độ đề xuất. {pollError.message}
          </p>
          <Button variant="secondary" onClick={restartPolling}>
            Tải lại
          </Button>
        </div>
      )}
      {showStart && (
        <div className={styles.actions}>
          {documents.map((doc) => (
            <Button
              key={doc.docId}
              variant="secondary"
              busy={startingDoc === doc.docId}
              busyLabel="Đang gửi"
              aria-disabled={startingDoc !== null || undefined}
              onClick={() => void start(doc)}
            >
              {documents.length === 1 ? "Đề xuất khái niệm tự động" : `Đề xuất từ “${doc.title}”`}
            </Button>
          ))}
        </div>
      )}
      {startError !== null && (
        <p className={styles.error} role="alert">
          {startError.message}
        </p>
      )}
    </section>
  );
}
