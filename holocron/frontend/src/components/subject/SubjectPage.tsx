"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/common/LoadState";
import { type TabItem, Tabs } from "@/components/common/Tabs";
import { ConceptsTab } from "@/components/concepts/ConceptsTab";
import { DocumentsTab } from "@/components/documents/DocumentsTab";
import { PracticeView } from "@/components/practice/PracticeView";
import { useShell } from "@/components/shell/ShellContext";
import { SkyView } from "@/components/sky/SkyView";
import { api } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import type { PairsLocation } from "@/lib/pairs";
import { writeRecentSubject } from "@/lib/recent-subject";
import { buildSkyLayout, type SkyLayout } from "@/lib/sky";
import { NO_SKY_GAINS, type SkyGains, skyGains } from "@/lib/sky-gains";
import {
  INITIAL_SKY_LOAD,
  SKY_LOAD_TIMEOUT_MS,
  type SkyLoadEvent,
  type SkyLoadState,
  skyLoadReducer,
} from "@/lib/sky-load";
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

/** `idPrefix` của hàng tab môn. */
const TABS_ID_PREFIX = "subject";

const TAB_ITEMS: readonly TabItem<SubjectTab>[] = [
  { id: "documents", label: "Tài liệu", icon: "document" },
  { id: "concepts", label: "Khái niệm", icon: "concept" },
  { id: "practice", label: "Ôn tập", icon: "practice" },
];

/**
 * `id` của tab Khái niệm (`Tabs` đặt `${idPrefix}-tab-${chỉ số}`), tính từ vị trí tab trong `TAB_ITEMS`: xem lại xong
 * thì focus vào đây.
 */
const CONCEPTS_TAB_ID = `${TABS_ID_PREFIX}-tab-${TAB_ITEMS.findIndex((item) => item.id === "concepts")}`;

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
  // Lượt tải lại bầu trời do tab Khái niệm yêu cầu (nhận, bỏ, tạo khái niệm): nối `SubjectPanes` với `SubjectSky`.
  const [skyReloads, setSkyReloads] = useState(0);
  const onConceptsChanged = useCallback(() => setSkyReloads((count) => count + 1), []);

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
          reloads={skyReloads}
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
          onConceptsChanged={onConceptsChanged}
        />
      )}
    </section>
  );
}

interface SkyRequest {
  code: string;
  tab: SubjectTab;
  /** Số lượt tải lại do tab Khái niệm yêu cầu, lần gần nhất đã áp. */
  reloads: number;
  /** Tăng mỗi lần cần tải lại (bấm "Thử lại", cửa sổ lấy lại focus). */
  attempt: number;
}

/**
 * Bầu trời 790×200 dưới tiêu đề trang môn, từ cặp khái niệm của môn (`GET /v1/vault/concept-pairs`). Tải lại khi đổi
 * tab, khi cửa sổ lấy lại focus và khi tab Khái niệm báo danh sách vừa đổi (`reloads` tăng); sao vàng vừa sáng thêm so
 * với lần vẽ trước được gắn `m-star-new`. Đang tải hay lỗi không chặn các tab ở dưới; lỗi khi đã có bầu trời thì giữ
 * bầu trời cũ kèm dòng "Chưa cập nhật được", chưa có thì hiện ô tối với một câu và nút "Thử lại".
 */
function SubjectSky({ code, title, tab, reloads }: { code: string; title: string; tab: SubjectTab; reloads: number }) {
  const [state, setState] = useState<SkyLoadState>(INITIAL_SKY_LOAD);
  const [request, setRequest] = useState<SkyRequest>({ code, tab, reloads, attempt: 0 });
  if (request.code !== code || request.tab !== tab || request.reloads !== reloads) {
    setRequest({ code, tab, reloads, attempt: request.attempt + 1 });
  }

  useEffect(() => {
    function reload(): void {
      setRequest((previous) => ({ ...previous, attempt: previous.attempt + 1 }));
    }
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, []);

  useEffect(() => {
    let alive = true;
    function send(event: SkyLoadEvent): void {
      if (alive) setState((previous) => skyLoadReducer(previous, event));
    }
    // Chuyển trạng thái ở `lib/sky-load.ts`; ở đây chỉ nối sự kiện. Lời gọi treo thì hết giờ báo lỗi, trả lời đến muộn
    // vẫn hiện bầu trời. `lib/api.ts` chưa nhận `AbortSignal` nên lời gọi hết giờ không huỷ được, chỉ bị bỏ qua khi rời
    // màn hoặc khi có lần tải mới.
    send({ type: "started" });
    const timer = window.setTimeout(() => send({ type: "timedOut" }), SKY_LOAD_TIMEOUT_MS);
    api.listConceptPairs({ subjectCode: request.code, chapterCode: null, status: null }).then(
      (list) => {
        window.clearTimeout(timer);
        send({ type: "succeeded", pairs: list.pairs });
      },
      () => {
        window.clearTimeout(timer);
        send({ type: "failed" });
      },
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [request]);

  const pairs = state.kind === "ready" ? state.pairs : null;
  const layout = useMemo(() => (pairs === null ? null : buildSkyLayout(title, pairs)), [title, pairs]);

  // So với bố cục vẽ lần trước để biết sao nào vừa sáng thêm (lần vẽ đầu không có sao mới).
  const drawn = useRef<SkyLayout | null>(null);
  const [gains, setGains] = useState<SkyGains>(NO_SKY_GAINS);
  useEffect(() => {
    if (layout === null) return;
    setGains(skyGains(drawn.current, layout));
    drawn.current = layout;
  }, [layout]);

  if (layout !== null) {
    return (
      <SkyView size="full" status="ready" layout={layout} gains={gains} stale={state.kind === "ready" && state.stale} />
    );
  }
  if (state.kind === "error") {
    return (
      <SkyView
        size="full"
        status="error"
        onRetry={() => {
          setState((previous) => skyLoadReducer(previous, { type: "started" }));
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
  /** Tab Khái niệm vừa đổi danh sách khái niệm: tải lại bầu trời. */
  onConceptsChanged: () => void;
}

function SubjectPanes({
  code,
  tab,
  documentLocation,
  listError,
  onReloadSubjects,
  onConceptsChanged,
}: SubjectPanesProps) {
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
      idPrefix={TABS_ID_PREFIX}
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
      {tab === "concepts" && (
        <ConceptsTab
          subjectCode={code}
          hrefFor={conceptsHref}
          onOpenDocuments={() => router.push(subjectHref(code, "documents"))}
          onOpenPractice={() => router.push(subjectHref(code, "practice"))}
          onConceptsChanged={onConceptsChanged}
        />
      )}
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
