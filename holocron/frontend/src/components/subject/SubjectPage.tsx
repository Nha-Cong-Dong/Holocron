"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect } from "react";

import { EmptyState } from "@/components/common/LoadState";
import { type TabItem, Tabs } from "@/components/common/Tabs";
import { PairsView } from "@/components/pairs/PairsView";
import { PracticeView } from "@/components/practice/PracticeView";
import { useShell } from "@/components/shell/ShellContext";
import { DocumentsPanel } from "@/components/vault/DocumentsPanel";
import { PageReview } from "@/components/vault/PageReview";
import type { UiError } from "@/lib/errors";
import type { PairsLocation } from "@/lib/pairs";
import {
  parseSubjectDocumentLocation,
  parseSubjectLocation,
  type SubjectDocumentLocation,
  subjectConceptsHref,
  subjectDocumentsHref,
  subjectHref,
} from "@/lib/subject-route";
import { canShowPanes, subjectTitle, subjectViewOf } from "@/lib/subject-view";
import type { SubjectTab } from "@/lib/tabs";
import type { VaultLocation } from "@/lib/vault";

import styles from "./SubjectPage.module.css";

const TAB_ITEMS: readonly TabItem<SubjectTab>[] = [
  { id: "documents", label: "Tài liệu", icon: "document" },
  { id: "concepts", label: "Khái niệm", icon: "concept" },
  { id: "practice", label: "Ôn tập", icon: "practice" },
];

/**
 * Trang môn `/subject?code=&tab=` (design-handoff/README.md màn 3): tiêu đề là tên môn (chưa tải ⇒ mã môn), rồi ba
 * phần Tài liệu · Khái niệm · Ôn tập. Mã môn đọc từ địa chỉ và truyền làm prop cho từng phần; khung được báo qua
 * `selectSubject` để hộp lệnh và các route cũ biết môn mở gần nhất. Phần Tài liệu đọc thêm `?doc=&page=` để xem lại
 * một trang ngay trong tab. Phải nằm trong `<Suspense>` vì đọc `useSearchParams()`.
 */
export function SubjectPage() {
  const params = useSearchParams();
  const location = parseSubjectLocation(params);
  const documentLocation = parseSubjectDocumentLocation(params);
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
      {canShowPanes(view) && (
        <SubjectPanes
          key={view.code}
          code={view.code}
          tab={location.tab}
          documentLocation={documentLocation}
          listError={view.kind === "list_unavailable" ? view.error : null}
          onReloadSubjects={reloadSubjects}
        />
      )}
    </section>
  );
}

interface SubjectPanesProps {
  code: string;
  tab: SubjectTab;
  documentLocation: SubjectDocumentLocation;
  /** Lỗi tải danh sách môn; chỉ phần Ôn tập cần danh sách (hình thức thi của môn) nên chỉ phần đó báo. */
  listError: UiError | null;
  onReloadSubjects: () => void;
}

function SubjectPanes({ code, tab, documentLocation, listError, onReloadSubjects }: SubjectPanesProps) {
  const router = useRouter();
  const conceptsHref = useCallback(
    (pairsLocation: Partial<PairsLocation>) => subjectConceptsHref(code, pairsLocation),
    [code],
  );
  const documentsHref = useCallback(
    (vaultLocation: Partial<VaultLocation>) => subjectDocumentsHref(code, vaultLocation),
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
      {tab === "documents" &&
        (documentLocation.doc === null ? (
          <DocumentsPanel subjectCode={code} hrefFor={documentsHref} />
        ) : (
          <PageReview
            key={documentLocation.doc}
            documentId={documentLocation.doc}
            pageNumber={documentLocation.page ?? 1}
            hrefFor={documentsHref}
          />
        ))}
      {tab === "concepts" && <PairsView subjectCode={code} hrefFor={conceptsHref} documentsHrefFor={documentsHref} />}
      {tab === "practice" &&
        (listError === null ? (
          <PracticeView subjectCode={code} hrefFor={conceptsHref} />
        ) : (
          <SubjectsListNotice error={listError} onRetry={onReloadSubjects} />
        ))}
    </Tabs>
  );
}

/**
 * Phần Ôn tập khi chưa có danh sách môn: một hướng dẫn ngắn. Lỗi tạm thời có đúng một nút "Thử lại" (tải lại danh
 * sách môn); bản đang chạy chưa có danh sách môn thì thử lại không giúp gì nên không có nút.
 */
function SubjectsListNotice({ error, onRetry }: { error: UiError; onRetry: () => void }) {
  if (error.kind === "not_found") {
    return (
      <EmptyState title="Phần Ôn tập cần danh sách môn, mà bản Holocron đang chạy chưa có.">
        Cập nhật Holocron lên bản mới hơn để tạo đề ôn tập. Phần Tài liệu và Khái niệm vẫn dùng được.
      </EmptyState>
    );
  }
  return (
    <EmptyState title="Chưa tải được danh sách môn nên chưa tạo được đề ôn tập.">
      <p>Kiểm tra kết nối ở thanh trạng thái phía dưới, rồi tải lại danh sách môn.</p>
      <button type="button" className={`m-btn-secondary ${styles.retry}`} onClick={onRetry}>
        Thử lại
      </button>
    </EmptyState>
  );
}
