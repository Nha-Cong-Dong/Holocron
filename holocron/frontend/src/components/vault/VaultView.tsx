"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { useShell } from "@/components/shell/ShellContext";
import { USE_MOCKS } from "@/lib/config";
import { parseVaultLocation, vaultHref } from "@/lib/vault";

import { ChunkBrowser } from "./ChunkBrowser";
import { DocumentsPanel } from "./DocumentsPanel";
import { PageReview } from "./PageReview";
import { SubjectHome } from "./SubjectHome";
import styles from "./Vault.module.css";

/**
 * `/vault`: môn học, nhập tài liệu PDF, xem lại trang và kho tri thức (kế hoạch UI mục F2). Mọi tham số đi qua query
 * string để build được ở chế độ static export: `?subject=` (môn), `&tab=chunks` (kho tri thức), `?doc=&page=` (xem
 * lại một trang). Phải nằm trong `<Suspense>` vì đọc `useSearchParams()`.
 *
 * Môn trên địa chỉ và môn chọn ở thanh bên được giữ khớp nhau: mở địa chỉ có `?subject=` thì chọn môn đó ở thanh bên;
 * chọn môn khác ở thanh bên thì chuyển tới danh sách tài liệu của môn đó.
 */
export function VaultView() {
  const location = parseVaultLocation(useSearchParams());
  const router = useRouter();
  const { subjectCode, selectSubject } = useShell();
  const synced = useRef<{ url: string | null; shell: string | null } | null>(null);

  useEffect(() => {
    const previous = synced.current;
    synced.current = { url: location.subject, shell: subjectCode };
    // Lần đầu hoặc địa chỉ đổi: địa chỉ thắng.
    if (previous === null || previous.url !== location.subject) {
      if (location.subject !== null && location.subject !== subjectCode) selectSubject(location.subject);
      return;
    }
    // Người dùng chọn môn khác ở thanh bên.
    if (previous.shell !== subjectCode && subjectCode !== null && subjectCode !== location.subject) {
      router.push(vaultHref({ subject: subjectCode }));
    }
  }, [location.subject, subjectCode, selectSubject, router]);

  const effectiveSubject = location.subject ?? (location.doc === null ? subjectCode : null);

  return (
    <div className={styles.page}>
      {USE_MOCKS && (
        <p className={styles.note}>
          Đang bật dữ liệu mẫu: môn học, tài liệu mẫu “Lịch ôn tập” của môn MAU101 và kết quả sửa, xác nhận của nó là
          bản dựng sẵn. Tài liệu bạn tự nhập vẫn đi qua backend thật.
        </p>
      )}
      {location.doc !== null ? (
        <PageReview key={location.doc} documentId={location.doc} pageNumber={location.page ?? 1} />
      ) : effectiveSubject === null ? (
        <SubjectHome />
      ) : (
        <>
          <nav className={styles.tabs} aria-label="Mục của môn">
            <Link
              className={styles.tab}
              href={vaultHref({ subject: effectiveSubject })}
              aria-current={location.tab === "documents" ? "page" : undefined}
            >
              Tài liệu
            </Link>
            <Link
              className={styles.tab}
              href={vaultHref({ subject: effectiveSubject, tab: "chunks" })}
              aria-current={location.tab === "chunks" ? "page" : undefined}
            >
              Kho tri thức
            </Link>
          </nav>
          {location.tab === "chunks" ? (
            <ChunkBrowser key={effectiveSubject} subjectCode={effectiveSubject} />
          ) : (
            <DocumentsPanel key={effectiveSubject} subjectCode={effectiveSubject} />
          )}
        </>
      )}
    </div>
  );
}
