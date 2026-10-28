"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { type BreadcrumbItem, breadcrumbForLocation } from "@/lib/subject-view";

import { useShell } from "./ShellContext";
import styles from "./TopBar.module.css";

function Chevron() {
  return (
    <svg className={styles.crumbSep} viewBox="0 0 24 24" width={16} height={16} aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

/** Breadcrumb của thanh trên: mục có `href` là liên kết, mục cuối luôn mang `aria-current="page"`. */
export function Breadcrumb({ items }: { items: readonly BreadcrumbItem[] }) {
  if (items.length === 0) return null;
  const lastIndex = items.length - 1;
  return (
    <nav className={styles.crumb} aria-label="Vị trí">
      <ol className={styles.crumbList}>
        {items.map((item, index) => {
          const isLast = index === lastIndex;
          return (
            <li key={`${item.href ?? "current"}:${item.label}`} className={styles.crumbItem}>
              {index > 0 && <Chevron />}
              {item.href !== null && !isLast ? (
                <Link className={styles.crumbLink} href={item.href}>
                  {item.label}
                </Link>
              ) : item.href !== null ? (
                <Link className={styles.crumbCurrent} href={item.href} aria-current="page">
                  {item.label}
                </Link>
              ) : (
                <span className={styles.crumbCurrent} aria-current={isLast ? "page" : undefined}>
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * Breadcrumb theo địa chỉ hiện tại: môn của trang `/subject` đọc từ `?code=` (không từ state của khung) cùng danh sách
 * môn. Đọc `useSearchParams()` nên phải nằm trong `<Suspense>` (thanh trên bọc sẵn); khung không đọc query.
 */
export function LocationBreadcrumb() {
  const pathname = usePathname();
  const params = useSearchParams();
  const { subjects } = useShell();
  return <Breadcrumb items={breadcrumbForLocation(pathname, params, subjects)} />;
}
