"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, toUiError } from "@/lib/api";
import type { UiError } from "@/lib/errors";
import { NAV_ITEMS, navItemForPath } from "@/lib/navigation";
import type { Subject } from "@/types/contract-pending";

import styles from "./Sidebar.module.css";

type SubjectsState = { kind: "loading" } | { kind: "error"; error: UiError } | { kind: "ready"; subjects: Subject[] };

interface SidebarProps {
  pathname: string;
  subjectCode: string | null;
  onSelectSubject: (subjectCode: string) => void;
}

/**
 * Thanh bên: danh sách môn (`GET /v1/subjects`, §6.8) và điều hướng chính. Dưới 1100 px chỉ còn chữ viết tắt; phần
 * chữ bị ẩn khi đó không chứa phần tử nhận focus.
 */
export function Sidebar({ pathname, subjectCode, onSelectSubject }: SidebarProps) {
  const [subjects, setSubjects] = useState<SubjectsState>({ kind: "loading" });
  const activeHref = navItemForPath(pathname)?.href ?? null;

  const load = useCallback(() => {
    setSubjects({ kind: "loading" });
    api
      .listSubjects()
      .then((list) => setSubjects({ kind: "ready", subjects: list }))
      .catch((error: unknown) => setSubjects({ kind: "error", error: toUiError(error) }));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <aside className={styles.sidebar} aria-label="Thanh bên">
      <p className={styles.brand}>
        <span className={styles.brandFull}>Holocron</span>
        <span className={styles.brandShort} aria-hidden="true">
          H
        </span>
      </p>

      <section className={styles.section} aria-labelledby="sidebar-subjects">
        <h2 id="sidebar-subjects" className={styles.sectionTitle}>
          Môn học
        </h2>
        {subjects.kind === "loading" && <p className={styles.note}>Đang tải…</p>}
        {subjects.kind === "error" && subjects.error.kind === "not_found" && (
          // Backend chưa có `GET /v1/subjects` (§6.8): trạng thái trống bình thường, không phải lỗi cần báo động.
          <p className={styles.note}>
            Chưa có danh sách môn. Bạn vẫn nhập đề thi được ở mục "Nhập đề thi"; môn học sẽ hiện ở đây khi backend hỗ
            trợ.
          </p>
        )}
        {subjects.kind === "error" && subjects.error.kind !== "not_found" && (
          <div className={styles.errorBlock}>
            <p className={styles.note} role="alert">
              Chưa tải được danh sách môn.
            </p>
            {/* Nút nằm ngoài phần chữ bị ẩn ở thanh bên hẹp, nên luôn nhìn thấy khi nhận focus. */}
            {subjects.error.action !== "none" && (
              <button type="button" className={styles.linkButton} onClick={load} aria-label="Thử tải lại danh sách môn">
                Thử lại
              </button>
            )}
          </div>
        )}
        {subjects.kind === "ready" && subjects.subjects.length === 0 && (
          <p className={styles.note}>Chưa có môn nào. Môn được tạo khi nhập tài liệu đầu tiên.</p>
        )}
        {subjects.kind === "ready" && subjects.subjects.length > 0 && (
          <ul className={styles.list}>
            {subjects.subjects.map((subject) => (
              <li key={subject.subject_code}>
                <button
                  type="button"
                  className={styles.item}
                  aria-pressed={subject.subject_code === subjectCode}
                  title={`${subject.subject_code} · ${subject.display_name}`}
                  onClick={() => onSelectSubject(subject.subject_code)}
                >
                  <span className={styles.code}>{subject.subject_code}</span>
                  <span className={styles.label}>{subject.display_name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <nav className={styles.section} aria-labelledby="sidebar-nav">
        <h2 id="sidebar-nav" className={styles.sectionTitle}>
          Điều hướng
        </h2>
        <ul className={styles.list}>
          {NAV_ITEMS.map((item) => (
            <li key={item.href}>
              <Link
                href={item.href}
                className={styles.item}
                aria-current={item.href === activeHref ? "page" : undefined}
                title={item.label}
              >
                <span className={styles.short} aria-hidden="true">
                  {item.short}
                </span>
                <span className={styles.label}>{item.label}</span>
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
