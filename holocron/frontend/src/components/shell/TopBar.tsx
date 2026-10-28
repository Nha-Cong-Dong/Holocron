"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { Icon } from "@/components/common/Icon";
import type { BreadcrumbItem } from "@/lib/subject-view";

import { Breadcrumb } from "./Breadcrumb";
import { ThemeToggle } from "./ThemeToggle";
import styles from "./TopBar.module.css";

interface TopBarProps {
  crumbs: readonly BreadcrumbItem[];
  /** Đang ở trang Cài đặt: nút "Cài đặt" mang `aria-current="page"`. */
  settingsCurrent: boolean;
  onOpenPalette: () => void;
  onOpenHelp: () => void;
}

/**
 * Thanh trên duy nhất của app (design-handoff/README.md màn 3, thay thanh bên): logo về trang chủ (mỗi lần bấm xoay
 * thêm 120°), breadcrumb, nút hộp lệnh `Ctrl+K`, bảng phím tắt `?`, đổi theme và "Cài đặt".
 */
export function TopBar({ crumbs, settingsCurrent, onOpenPalette, onOpenHelp }: TopBarProps) {
  const router = useRouter();
  const [turns, setTurns] = useState(0);

  return (
    <header className={styles.top}>
      <div className={styles.inner}>
        <button
          type="button"
          className={styles.brand}
          aria-label="Holocron, về trang chủ"
          onClick={() => {
            setTurns((value) => value + 1);
            router.push("/");
          }}
        >
          <HolocronMark size={30} turns={turns} />
          <span aria-hidden="true">Holocron</span>
        </button>
        <Breadcrumb items={crumbs} />
        <div className={styles.actions}>
          <button
            type="button"
            className={`m-btn-icon ${styles.iconButton}`}
            onClick={onOpenPalette}
            aria-label="Mở hộp lệnh (Ctrl+K)"
            title="Mở hộp lệnh (Ctrl+K)"
          >
            <kbd className={styles.kbd}>Ctrl K</kbd>
          </button>
          <button
            type="button"
            className={`m-btn-icon ${styles.iconButton}`}
            onClick={onOpenHelp}
            aria-label="Bảng phím tắt (?)"
            title="Bảng phím tắt (?)"
          >
            <kbd className={styles.kbd}>?</kbd>
          </button>
          <ThemeToggle />
          <Link
            href="/settings"
            className={`m-btn-secondary ${styles.settings}`}
            aria-current={settingsCurrent ? "page" : undefined}
          >
            <Icon name="settings" size={20} />
            Cài đặt
          </Link>
        </div>
      </div>
    </header>
  );
}
