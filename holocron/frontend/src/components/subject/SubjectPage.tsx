"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState } from "@/components/common/LoadState";
import { type TabItem, Tabs } from "@/components/common/Tabs";
import { DocumentsTab } from "@/components/documents/DocumentsTab";
import { PairsView } from "@/components/pairs/PairsView";
import { PracticeView } from "@/components/practice/PracticeView";
import { useShell } from "@/components/shell/ShellContext";
import { SkyView } from "@/components/sky/SkyView";
import { api } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import type { PairsLocation } from "@/lib/pairs";
import { writeRecentSubject } from "@/lib/recent-subject";
import { buildSkyLayout, type SkyPair } from "@/lib/sky";
import {
  parseSubjectDocumentLocation,
  parseSubjectLocation,
  type SubjectDocumentLocation,
  subjectConceptsHref,
  subjectDocumentsHref,
  subjectHref,
} from "@/lib/subject-route";
import { canShowPanes, canShowSky, subjectTitle, subjectViewOf } from "@/lib/subject-view";
import type { SubjectTab } from "@/lib/tabs";
import { browserStorage } from "@/lib/theme";
import type { VaultLocation } from "@/lib/vault";

import styles from "./SubjectPage.module.css";

/** `id` của tab Khái niệm (`Tabs` đặt `${idPrefix}-tab-${chỉ số}`): xem lại xong thì focus vào đây. */
const CONCEPTS_TAB_ID = "subject-tab-1";

const TAB_ITEMS: readonly TabItem<SubjectTab>[] = [
  { id: "documents", label: "Tài liệu", icon: "document" },
  { id: "concepts", label: "Khái niệm", icon: "concept" },
  { id: "practice", label: "Ôn tập", icon: "practice" },
];

/**
 * Trang môn `/subject?code=&tab=` (design-handoff/README.md màn 3): tiêu đề là tên môn (chưa tải ⇒ mã môn), bầu trời
 * kiến thức của môn, rồi ba phần Tài liệu · Khái niệm · Ôn tập. Mã môn đọc từ địa chỉ và truyền làm prop cho từng
 * phần; khung được báo qua `selectSubject` (và mã được lưu làm môn gần nhất cho trang chủ) để hộp lệnh và các route cũ
 * biết môn mở gần nhất. Phần Tài liệu (`DocumentsTab`, màn 4–6) đọc thêm `?doc=&page=` để xem lại một tài liệu ngay
 * trong tab; xem lại xong thì sang tab Khái niệm. Phải nằm trong
 * `<Suspense>` vì đọc `useSearchParams()`.
 */
export function SubjectPage() {
  const params = useSearchParams();
  const location = parseSubjectLocation(params);
  const documentLocation = parseSubjectDocumentLocation(params);
  const { subjects, reloadSubjects, selectSubject } = useShell();
  const code = location.code;

  useEffect(() => {
    if (code === null) return;
    selectSubject(code);
    // Trang chủ đặt nút chính ở thẻ của môn mở gần nhất.
    writeRecentSubject(browserStorage(), code);
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
      {canShowSky(view) && (
        <SubjectSky
          key={`sky-${view.code}`}
          code={view.code}
          title={subjectTitle(view) ?? view.code}
          tab={location.tab}
        />
      )}
      {canShowPanes(view) && (
        <SubjectPanes
          key={`panes-${view.code}`}
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

/** Thời gian chờ tối đa một lần tải bầu trời trước khi báo lỗi. */
const SKY_LOAD_TIMEOUT_MS = 15_000;

type SkyState =
  | { kind: "loading" }
  | { kind: "error" }
  /** `stale`: lần tải lại gần nhất lỗi, đang giữ bầu trời của lần trước. */
  | { kind: "ready"; pairs: readonly SkyPair[]; stale: boolean };

interface SkyRequest {
  code: string;
  tab: SubjectTab;
  /** Tăng mỗi lần cần tải lại (bấm "Thử lại", cửa sổ lấy lại focus). */
  attempt: number;
}

/**
 * Bầu trời 790×200 dưới tiêu đề trang môn, từ cặp khái niệm của môn (`GET /v1/vault/concept-pairs`). Tải lại khi đổi
 * tab và khi cửa sổ lấy lại focus. Đang tải hay lỗi không chặn các tab ở dưới; lỗi khi đã có bầu trời thì giữ bầu
 * trời cũ kèm dòng "Chưa cập nhật được", chưa có thì hiện ô tối với một câu và nút "Thử lại".
 */
function SubjectSky({ code, title, tab }: { code: string; title: string; tab: SubjectTab }) {
  const [state, setState] = useState<SkyState>({ kind: "loading" });
  const [request, setRequest] = useState<SkyRequest>({ code, tab, attempt: 0 });
  if (request.code !== code || request.tab !== tab) setRequest({ code, tab, attempt: request.attempt + 1 });

  useEffect(() => {
    function reload(): void {
      setRequest((previous) => ({ ...previous, attempt: previous.attempt + 1 }));
    }
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, []);

  useEffect(() => {
    let alive = true;
    function fail(): void {
      if (!alive) return;
      setState((previous) => (previous.kind === "ready" ? { ...previous, stale: true } : { kind: "error" }));
    }
    // Lời gọi treo (không trả lời, không lỗi) không được giữ ô trời ở "Đang tải" mãi: hết giờ thì báo lỗi; nếu sau đó
    // backend vẫn trả lời thì bầu trời hiện như thường.
    const timer = window.setTimeout(fail, SKY_LOAD_TIMEOUT_MS);
    api.listConceptPairs({ subjectCode: request.code, chapterCode: null, status: null }).then(
      (list) => {
        window.clearTimeout(timer);
        if (alive) setState({ kind: "ready", pairs: list.pairs, stale: false });
      },
      () => {
        window.clearTimeout(timer);
        fail();
      },
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [request]);

  const pairs = state.kind === "ready" ? state.pairs : null;
  const layout = useMemo(() => (pairs === null ? null : buildSkyLayout(title, pairs)), [title, pairs]);

  if (layout !== null) {
    return <SkyView size="full" status="ready" layout={layout} stale={state.kind === "ready" && state.stale} />;
  }
  if (state.kind === "error") {
    return (
      <SkyView
        size="full"
        status="error"
        onRetry={() => {
          setState({ kind: "loading" });
          setRequest((previous) => ({ ...previous, attempt: previous.attempt + 1 }));
        }}
      />
    );
  }
  return <SkyView size="full" status="loading" />;
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
  // Xem lại xong một tài liệu: sang tab Khái niệm và đặt focus vào tab đó (design-handoff/README.md màn 6).
  const onDocumentFinished = useCallback(() => {
    router.push(subjectHref(code, "concepts"));
    requestAnimationFrame(() => document.getElementById(CONCEPTS_TAB_ID)?.focus());
  }, [router, code]);

  return (
    <Tabs
      label="Phần của môn"
      idPrefix="subject"
      items={TAB_ITEMS}
      selected={tab}
      onSelect={(next) => router.push(subjectHref(code, next))}
    >
      {tab === "documents" && (
        <DocumentsTab
          subjectCode={code}
          location={documentLocation}
          hrefFor={documentsHref}
          onFinished={onDocumentFinished}
        />
      )}
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
