"use client";

import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { api, toUiError } from "@/lib/api";
import { USE_MOCKS } from "@/lib/config";
import { createLatestCounter } from "@/lib/latest";
import { NAV_ITEMS, titleForPath } from "@/lib/navigation";
import { INITIAL_RUNTIME_STATE, runtimeReducer, selectOfflineMode, selectStatusItems } from "@/lib/runtime";
import { GLOBAL_SCOPE, resolveShortcut, targetKindOf } from "@/lib/shortcuts";
import type { SubjectsState } from "@/lib/subjects";

import styles from "./AppShell.module.css";
import { CommandPalette, type PaletteCommand } from "./CommandPalette";
import { describeTarget } from "./keyboard";
import { OfflineBanner } from "./OfflineBanner";
import { ShellContext, type ShellContextValue } from "./ShellContext";
import { ShortcutHelp } from "./ShortcutHelp";
import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";

type Overlay = "none" | "palette" | "help";

/**
 * Khung app desktop: thanh bên (môn + điều hướng), vùng nội dung, thanh trạng thái khả năng, hộp lệnh `Ctrl+K` và
 * bảng phím tắt `?`. Trạng thái chạy đọc từ `GET /v1/runtime` một lần khi mở app và khi người dùng bấm "Kiểm tra lại".
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
      ...NAV_ITEMS.map((item) => ({
        id: `nav:${item.href}`,
        label: `Đi tới: ${item.label}`,
        hint: item.description,
        run: () => router.push(item.href),
      })),
      { id: "help", label: "Bảng phím tắt", hint: "Phím ?", run: () => setOverlay("help") },
      {
        id: "runtime",
        label: "Kiểm tra lại kết nối backend",
        hint: "Đọc lại trạng thái và khả năng",
        run: refreshRuntime,
      },
    ],
    [router, refreshRuntime],
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

  return (
    <ShellContext.Provider value={context}>
      <a className={styles.skipLink} href="#main-content">
        Bỏ qua tới nội dung chính
      </a>
      <div className={styles.root}>
        <div className={styles.frame}>
          <Sidebar
            pathname={pathname}
            subjects={subjects}
            onReloadSubjects={reloadSubjects}
            subjectCode={subjectCode}
            onSelectSubject={setSubjectCode}
          />
          <div className={styles.workspace}>
            <header className={styles.header}>
              <h1 className={styles.title}>{titleForPath(pathname)}</h1>
              <div className={styles.headerActions}>
                <button type="button" className={styles.headerButton} onClick={() => setOverlay("palette")}>
                  Đi tới… <kbd>Ctrl+K</kbd>
                </button>
                <button
                  type="button"
                  className={styles.headerButton}
                  onClick={() => setOverlay("help")}
                  aria-label="Bảng phím tắt"
                  title="Bảng phím tắt (?)"
                >
                  <kbd>?</kbd>
                </button>
              </div>
            </header>
            {selectOfflineMode(runtime) && <OfflineBanner />}
            <main id="main-content" className={styles.content} tabIndex={-1}>
              <div className={styles.contentInner}>{children}</div>
            </main>
          </div>
        </div>
        <StatusBar
          items={selectStatusItems(runtime, USE_MOCKS)}
          canRetry={runtime.backend === "unreachable" || runtime.backend === "error"}
          onRetry={refreshRuntime}
        />
      </div>
      <CommandPalette open={overlay === "palette"} onClose={closeOverlay} commands={commands} />
      <ShortcutHelp open={overlay === "help"} onClose={closeOverlay} />
    </ShellContext.Provider>
  );
}
