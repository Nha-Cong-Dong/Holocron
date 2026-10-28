"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect } from "react";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { EmptyState } from "@/components/common/LoadState";
import { type TabItem, Tabs } from "@/components/common/Tabs";
import { PairsView } from "@/components/pairs/PairsView";
import { PracticeView } from "@/components/practice/PracticeView";
import { useShell } from "@/components/shell/ShellContext";
import { DocumentsPanel } from "@/components/vault/DocumentsPanel";
import type { PairsLocation } from "@/lib/pairs";
import { parseSubjectLocation, subjectConceptsHref, subjectHref } from "@/lib/subject-route";
import { canShowPanes, subjectTitle, subjectViewOf } from "@/lib/subject-view";
import type { SubjectTab } from "@/lib/tabs";

import styles from "./SubjectPage.module.css";

const TAB_ITEMS: readonly TabItem<SubjectTab>[] = [
  { id: "documents", label: "Tài liệu", icon: "document" },
  { id: "concepts", label: "Khái niệm", icon: "concept" },
  { id: "practice", label: "Ôn tập", icon: "practice" },
];

/**
 * Trang môn `/subject?code=&tab=` (design-handoff/README.md màn 3): tiêu đề là tên môn (chưa tải ⇒ mã môn), rồi ba
 * phần Tài liệu · Khái niệm · Ôn tập. Mã môn đọc từ địa chỉ và truyền làm prop cho từng phần; khung được báo qua
 * `selectSubject` để thanh trên và hộp lệnh biết môn đang mở. Phải nằm trong `<Suspense>` vì đọc `useSearchParams()`.
 */
export function SubjectPage() {
  const location = parseSubjectLocation(useSearchParams());
  const { subjects, reloadSubjects, selectSubject } = useShell();
  const code = location.code;

  useEffect(() => {
    if (code !== null) selectSubject(code);
  }, [code, selectSubject]);

  const view = subjectViewOf(code, subjects);

  if (view.kind === "invalid_code") {
    return (
      <section className={styles.page} aria-labelledby="subject-title">
        <h1 id="subject-title" className={styles.title}>
          Chưa mở được môn
        </h1>
        <EmptyState title="Địa chỉ thiếu mã môn hoặc mã môn không đúng dạng.">
          <Link href="/">Về trang chủ</Link> để chọn một môn.
        </EmptyState>
      </section>
    );
  }

  if (view.kind === "not_found") {
    return (
      <section className={styles.page} aria-labelledby="subject-title">
        <h1 id="subject-title" className={styles.title}>
          {view.code}
        </h1>
        <EmptyState title={`Chưa có môn ${view.code} trong danh sách môn của bạn.`}>
          <Link href="/">Về trang chủ</Link> để chọn môn khác hoặc tạo môn mới.
        </EmptyState>
      </section>
    );
  }

  return (
    <section className={styles.page} aria-labelledby="subject-title">
      <header className={styles.head}>
        <h1 id="subject-title" className={styles.title}>
          {subjectTitle(view)}
        </h1>
        <p className={styles.meta}>Mã môn {view.code}</p>
      </header>
      {view.kind === "list_unavailable" && view.error.kind !== "not_found" && (
        <ErrorNotice error={view.error} onRetry={reloadSubjects} onReload={reloadSubjects} />
      )}
      {canShowPanes(view) && <SubjectPanes key={view.code} code={view.code} tab={location.tab} />}
    </section>
  );
}

function SubjectPanes({ code, tab }: { code: string; tab: SubjectTab }) {
  const router = useRouter();
  const conceptsHref = useCallback(
    (pairsLocation: Partial<PairsLocation>) => subjectConceptsHref(code, pairsLocation),
    [code],
  );

  return (
    <Tabs
      label="Phần của môn"
      idPrefix="subject"
      items={TAB_ITEMS}
      selected={tab}
      onSelect={(next) => router.push(subjectHref(code, next))}
    >
      {tab === "documents" && <DocumentsPanel subjectCode={code} />}
      {tab === "concepts" && <PairsView subjectCode={code} hrefFor={conceptsHref} />}
      {tab === "practice" && <PracticeView subjectCode={code} hrefFor={conceptsHref} />}
    </Tabs>
  );
}
