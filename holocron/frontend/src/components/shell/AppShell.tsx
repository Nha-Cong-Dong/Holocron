"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ImportQueueProvider } from "@/components/documents/ImportQueueProvider";
import { ToastProvider } from "@/components/ui/Toast";
import { api, toUiError } from "@/lib/api";
import { createLatestCounter } from "@/lib/latest";
import { shellHeadingForPath } from "@/lib/navigation";
import {
  freshRuntime,
  hasPendingLocalCheck,
  INITIAL_RUNTIME_STATE,
  runtimeReducer,
  selectOfflineMode,
} from "@/lib/runtime";
import { createRuntimePoller } from "@/lib/runtime-poll";
import { GLOBAL_SCOPE, resolveShortcut, targetKindOf } from "@/lib/shortcuts";
import { subjectHref } from "@/lib/subject-route";
import type { SubjectsState } from "@/lib/subjects";
import { announceThemeChange, applyMotion, browserStorage, MOTION_STORAGE_KEY, readMotion } from "@/lib/theme";

import styles from "./AppShell.module.css";
import { CommandPalette, type PaletteCommand } from "./CommandPalette";
import { describeTarget } from "./keyboard";
import { OfflineBanner } from "./OfflineBanner";
import { ShellContext, type ShellContextValue } from "./ShellContext";
import { ShortcutHelp } from "./ShortcutHelp";
import { TopBar } from "./TopBar";

type Overlay = "none" | "palette" | "help";

/**
 * Khung app desktop: thanh trên (logo, breadcrumb, theme, "Cài đặt"), vùng nội dung, hộp lệnh `Ctrl+K` và bảng phím
 * tắt `?`. Không còn thanh trạng thái dưới cùng: trạng thái kết nối, bản đang chạy và thư mục log nằm ở Cài đặt › Máy
 * của bạn › "Chi tiết kỹ thuật". Khung không đọc `useSearchParams`: breadcrumb tự đọc `?code=` trong
 * `<Suspense>` riêng của thanh trên; trang môn báo môn đang mở qua `selectSubject` cho các màn dùng môn của khung. Trạng thái chạy đọc từ `GET /v1/runtime` một lần khi mở app và khi người dùng bấm "Kiểm tra lại".
 */
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [runtime, dispatch] = useReducer(runtimeReducer, INITIAL_RUNTIME_STATE);
  const [overlay, setOverlay] = useState<Overlay>("none");
  const [subjectCode, setSubjectCode] = useState<string | null>(null);
  const [subjects, setSubjects] = useState<SubjectsState>({ kind: "loading" });
  // Bấm "Kiểm tra lại" liên tiếp: chỉ kết quả của lần gọi mới nhất được áp, lần cũ trả về muộn bị bỏ qua.
  const runtimeCalls = useRef(createLatestCounter());
  const subjectCalls = useRef(createLatestCounter());

  const refreshRuntime = useCallback(() => {
    const ticket = runtimeCalls.current.begin();
    dispatch({ type: "load_started" });
    api
      .getRuntime()
      .then((info) => {
        if (runtimeCalls.current.isLatest(ticket)) dispatch({ type: "load_succeeded", runtime: info });
      })
      .catch((error: unknown) => {
        if (runtimeCalls.current.isLatest(ticket)) dispatch({ type: "load_failed", error: toUiError(error) });
      });
  }, []);

  // Backend còn kiểm OCR nền (`local_check_pending`): hỏi lại im lặng (không đổi sang "đang kết nối" để ô trạng thái
  // không nhấp nháy); một lần lỗi mạng giữ nguyên runtime, lỗi liên tiếp đủ ngưỡng mới báo lỗi và hiện "Kiểm tra lại".
  const localCheckPending = hasPendingLocalCheck(freshRuntime(runtime));
  useEffect(() => {
    if (!localCheckPending) return;
    const poller = createRuntimePoller({ load: api.getRuntime, dispatch, toUiError });
    poller.start();
    return () => poller.stop();
  }, [localCheckPending]);

  const reloadSubjects = useCallback(() => {
    const ticket = subjectCalls.current.begin();
    setSubjects({ kind: "loading" });
    api
      .listSubjects()
      .then((list) => {
        if (subjectCalls.current.isLatest(ticket)) setSubjects({ kind: "ready", subjects: list });
      })
      .catch((error: unknown) => {
        if (subjectCalls.current.isLatest(ticket)) setSubjects({ kind: "error", error: toUiError(error) });
      });
  }, []);

  useEffect(() => {
    refreshRuntime();
    reloadSubjects();
    const runtimeCounter = runtimeCalls.current;
    const subjectCounter = subjectCalls.current;
    return () => {
      runtimeCounter.invalidate();
      subjectCounter.invalidate();
    };
  }, [refreshRuntime, reloadSubjects]);

  // Tab khác bật/tắt "Giảm chuyển động": áp ngay cho mọi trang của tab này (không chỉ khi đang mở Cài đặt), rồi báo
  // cho các nơi đang hiển thị lựa chọn đọc lại. Theme đổi ở tab khác do nút theme trên thanh trên xử lý.
  useEffect(() => {
    function onStorage(event: StorageEvent): void {
      if (event.key !== MOTION_STORAGE_KEY) return;
      applyMotion(document.documentElement, readMotion(browserStorage()));
      announceThemeChange();
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      const action = resolveShortcut(
        {
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          altKey: event.altKey,
          target: targetKindOf(describeTarget(event.target)),
        },
        { overlayOpen: overlay !== "none", scope: GLOBAL_SCOPE },
      );
      if (action === null) return;
      // `Esc` do `<dialog>` tự xử lý; các phím của màn luyện tập do màn đó xử lý (`usePracticeShortcuts`).
      if (action.type === "open_palette") {
        event.preventDefault();
        setOverlay("palette");
      } else if (action.type === "show_help") {
        event.preventDefault();
        setOverlay("help");
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [overlay]);

  const closeOverlay = useCallback(() => setOverlay("none"), []);

  const commands = useMemo<PaletteCommand[]>(
    () => [
      { id: "home", label: "Về trang chủ", hint: "Danh sách môn học", run: () => router.push("/") },
      ...(subjects.kind === "ready" ? subjects.subjects : []).map((subject) => ({
        id: `subject:${subject.subject_code}`,
        label: `Mở môn: ${subject.display_name.trim() || subject.subject_code}`,
        hint: subject.subject_code,
        run: () => router.push(subjectHref(subject.subject_code, "documents")),
      })),
      {
        id: "settings",
        label: "Cài đặt",
        hint: "Giao diện, giảm chuyển động, tính năng thử nghiệm",
        run: () => router.push("/settings"),
      },
      { id: "help", label: "Bảng phím tắt", hint: "Phím ?", run: () => setOverlay("help") },
      {
        id: "runtime",
        label: "Kiểm tra lại kết nối",
        hint: "Đọc lại trạng thái và khả năng của máy này",
        run: refreshRuntime,
      },
    ],
    [router, refreshRuntime, subjects],
  );

  const context = useMemo<ShellContextValue>(
    () => ({
      runtime,
      refreshRuntime,
      reportLlmCall: (outcome) =>
        dispatch({ type: outcome === "upstream_error" ? "llm_upstream_failed" : "llm_call_succeeded" }),
      subjects,
      reloadSubjects,
      subjectCode,
      selectSubject: setSubjectCode,
      overlayOpen: overlay !== "none",
    }),
    [runtime, refreshRuntime, subjects, reloadSubjects, subjectCode, overlay],
  );

  // Mỗi route có đúng một <h1>: trang tự có tiêu đề (trang môn, Cài đặt) thì khung không render thêm.
  const heading = shellHeadingForPath(pathname);

  return (
    <ShellContext.Provider value={context}>
      {/* Hàng đợi nhập tài liệu sống suốt phiên app: rời trang môn vẫn gửi tiếp. */}
      <ImportQueueProvider>
        <ToastProvider>
          <a className={styles.skipLink} href="#main-content">
            Bỏ qua tới nội dung chính
          </a>
          <div className={styles.root}>
            <TopBar
              settingsCurrent={pathname.replace(/\/+$/, "") === "/settings"}
              onOpenPalette={() => setOverlay("palette")}
              onOpenHelp={() => setOverlay("help")}
            />
            <div className={styles.body}>
              {selectOfflineMode(runtime) && <OfflineBanner />}
              <main id="main-content" className={styles.content} tabIndex={-1}>
                <div className={styles.contentInner}>
                  {heading !== null && (
                    <h1 className={heading.visuallyHidden ? "visually-hidden" : styles.pageTitle}>{heading.text}</h1>
                  )}
                  {children}
                </div>
              </main>
            </div>
          </div>
          <CommandPalette open={overlay === "palette"} onClose={closeOverlay} commands={commands} />
          <ShortcutHelp open={overlay === "help"} onClose={closeOverlay} />
        </ToastProvider>
      </ImportQueueProvider>
    </ShellContext.Provider>
  );
}
