"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { Icon } from "@/components/common/Icon";
import {
  BackendHelp,
  isUnreachable,
  MACHINE_SECTION_ID,
  TechnicalDetails,
} from "@/components/settings/MachineDiagnostics";
import { useShell } from "@/components/shell/ShellContext";
import { ErrorState, Skeleton } from "@/components/ui";
import { OpenByCode } from "@/components/vault/SubjectHome";
import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { buildHomeCards, runWithLimit, SUBJECT_STATS_CONCURRENCY, type SubjectStats } from "@/lib/home-cards";
import { readRecentSubject } from "@/lib/recent-subject";
import { freshRuntime, needsTesseractHelp } from "@/lib/runtime";
import { subjectHref } from "@/lib/subject-route";
import { browserStorage } from "@/lib/theme";
import type { Subject } from "@/types/holocron";

import styles from "./HomeScreen.module.css";
import { SubjectCard } from "./SubjectCard";

const SKELETON_CARDS = ["a", "b", "c"] as const;

/** Cách sửa khi không mở được danh sách môn, theo loại lỗi (mã kỹ thuật nằm trong "Chi tiết kỹ thuật"). */
function listErrorFix(error: UiError): string {
  if (isUnreachable(error)) return "Holocron chưa chạy xong hoặc đã tắt. Mở lại Holocron rồi bấm Thử lại.";
  if (error.kind === "busy") return "Thư mục dữ liệu của Holocron đang bận. Đợi vài giây rồi bấm Thử lại.";
  if (error.kind === "forbidden")
    return "Holocron từ chối yêu cầu từ cửa sổ này. Mở lại Holocron trên máy rồi bấm Thử lại.";
  return "Holocron trả lời không như mong đợi. Bấm Thử lại; nếu vẫn lỗi, mở lại Holocron.";
}

function HomeHeading() {
  return (
    <h1 id="home-title" className={styles.title}>
      Bầu trời của bạn
    </h1>
  );
}

/** Thiếu Tesseract: một dải nhỏ, nút "Xem cách cài" mở Cài đặt › Máy của bạn. */
function ScanWarning() {
  const { runtime } = useShell();
  if (!needsTesseractHelp(freshRuntime(runtime))) return null;
  return (
    <div className={styles.warn}>
      <Icon name="warning" size={20} />
      <span className={styles.warnText}>Chưa đọc được ảnh scan. File PDF có chữ sẵn vẫn đọc bình thường.</span>
      <Link className={styles.warnLink} href={`/settings#${MACHINE_SECTION_ID}`}>
        Xem cách cài
      </Link>
    </div>
  );
}

function HomeLoading() {
  return (
    <div className={styles.loading} aria-busy="true">
      <ul className={styles.grid} aria-hidden="true">
        {SKELETON_CARDS.map((key) => (
          <li key={key} className={styles.skeletonCard}>
            <Skeleton height={72} />
            <Skeleton width="55%" height={24} />
            <Skeleton width="80%" height={16} />
            <Skeleton width={110} height={44} className={styles.skeletonButton} />
          </li>
        ))}
      </ul>
      <output className={styles.loadingText}>
        <HolocronMark size={22} spinning />
        Đang mở các môn…
      </output>
    </div>
  );
}

function HomeListError({ error, onRetry }: { error: UiError; onRetry: () => void }) {
  if (error.kind === "not_found") {
    return (
      <>
        <ErrorState
          title="Chưa mở được danh sách môn"
          fix="Bản Holocron đang chạy chưa có danh sách môn. Mở môn bằng mã môn ở dưới; môn được tạo khi bạn thêm tài liệu đầu tiên."
          onRetry={onRetry}
          technical={error.diagnostic}
        />
        <OpenByCode hrefFor={(code) => subjectHref(code, "documents")} />
      </>
    );
  }
  return (
    <>
      <ErrorState title="Không mở được danh sách môn" fix={listErrorFix(error)} onRetry={onRetry} />
      <TechnicalDetails>
        <p>{error.message}</p>
        {error.diagnostic !== undefined && <code className={styles.code}>{error.diagnostic}</code>}
        {isUnreachable(error) && <BackendHelp />}
      </TechnicalDetails>
    </>
  );
}

interface SubjectGridProps {
  subjects: Subject[];
  onAddSubject: () => void;
}

/** Lưới thẻ môn; mỗi môn tải số liệu riêng (tối đa 4 môn cùng lúc), một thẻ lỗi không ảnh hưởng thẻ khác. */
function SubjectGrid({ subjects, onAddSubject }: SubjectGridProps) {
  const [stats, setStats] = useState<Record<string, SubjectStats>>({});
  const [recent, setRecent] = useState<string | null>(null);
  const alive = useRef(true);

  // Đọc sau khi mount: HTML dựng sẵn không biết `localStorage`.
  useEffect(() => {
    setRecent(readRecentSubject(browserStorage()));
  }, []);

  const loadOne = useCallback(async (code: string): Promise<void> => {
    setStats((previous) => ({ ...previous, [code]: { kind: "loading" } }));
    try {
      const [documents, pairs] = await Promise.all([
        api.listDocuments(code),
        api.listConceptPairs({ subjectCode: code, chapterCode: null, status: null }),
      ]);
      if (alive.current) {
        setStats((previous) => ({ ...previous, [code]: { kind: "ready", documents, pairs: pairs.pairs } }));
      }
    } catch (caught) {
      if (alive.current) {
        setStats((previous) => ({ ...previous, [code]: { kind: "error", error: toUiError(caught) } }));
      }
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void runWithLimit(
      subjects.map((subject) => subject.subject_code),
      SUBJECT_STATS_CONCURRENCY,
      loadOne,
    );
    return () => {
      alive.current = false;
    };
  }, [subjects, loadOne]);

  const cards = useMemo(() => buildHomeCards(subjects, stats, recent), [subjects, stats, recent]);

  return (
    <ul className={`m-stagger ${styles.grid}`}>
      {cards.map((card) => (
        <SubjectCard key={card.code} card={card} onRetry={() => void loadOne(card.code)} />
      ))}
      <li className={styles.addItem}>
        <button type="button" className={styles.addCard} onClick={onAddSubject}>
          <span className={styles.plus} aria-hidden="true">
            <svg viewBox="0 0 24 24" width="22" height="22" focusable="false" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </span>
          Thêm môn
        </button>
      </li>
    </ul>
  );
}

export interface HomeScreenProps {
  onAddSubject: () => void;
}

/**
 * Trang chủ "Bầu trời của bạn" (design-handoff/README.md màn 1) khi danh sách môn đang tải, lỗi hoặc có ít nhất một
 * môn (danh sách rỗng là màn chào). Đúng một nút chính trên trang: nút của thẻ môn dùng gần nhất.
 */
export function HomeScreen({ onAddSubject }: HomeScreenProps) {
  const { subjects, reloadSubjects } = useShell();
  return (
    <section className={`m-screen ${styles.home}`} aria-labelledby="home-title">
      <ScanWarning />
      <HomeHeading />
      {subjects.kind === "loading" && <HomeLoading />}
      {subjects.kind === "error" && <HomeListError error={subjects.error} onRetry={reloadSubjects} />}
      {subjects.kind === "ready" && <SubjectGrid subjects={subjects.subjects} onAddSubject={onAddSubject} />}
    </section>
  );
}
