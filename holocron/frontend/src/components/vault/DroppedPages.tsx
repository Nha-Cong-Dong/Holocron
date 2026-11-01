"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";

import type { PageSummaryView, PageView } from "@/lib/documents-data";
import { droppedReasonLabel, relatedPageLinks } from "@/lib/dropped-pages";
import { type ActiveOnShow, focusOnShow } from "@/lib/review-confirm";
import type { VaultHrefFor } from "@/lib/vault";

import styles from "./DroppedPages.module.css";
import vault from "./Vault.module.css";

interface DroppedPageNavProps {
  pages: readonly PageSummaryView[];
  currentPage: number | null;
  subjectCode: string;
  documentId: string;
  hrefFor: VaultHrefFor;
}

/**
 * Nhóm "Đã ẩn tự động" ở danh sách trang: các trang bỏ mềm, hiện mờ nhưng vẫn mở xem được (§5.7). Là liên kết thường
 * nên Tab và Enter dùng được như các trang khác. Không vẽ gì khi không có trang nào bị ẩn.
 */
export function DroppedPageNav({ pages, currentPage, subjectCode, documentId, hrefFor }: DroppedPageNavProps) {
  if (pages.length === 0) return null;
  return (
    <div className={styles.group}>
      <h3 className={styles.groupTitle} id="review-dropped-title">
        Đã ẩn tự động
      </h3>
      <ol className={vault.pageNav} aria-labelledby="review-dropped-title">
        {pages.map((item) => (
          <li key={item.pageNumber}>
            <Link
              className={`${vault.pageLink} ${styles.droppedLink}`}
              href={hrefFor({ subject: subjectCode, doc: documentId, page: item.pageNumber })}
              aria-current={item.pageNumber === currentPage ? "page" : undefined}
            >
              <span>Trang {item.pageNumber}</span>
              <span className={vault.pageLinkStatus}>Đã ẩn, chưa vào kho</span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

interface DroppedPagePanelProps {
  page: PageView;
  documentId: string;
  subjectCode: string;
  hrefFor: VaultHrefFor;
  /** Đang có thao tác ghi: khoá nút (chống bấm đúp ở `PageReview`, nơi giữ cờ gửi một lần). */
  busy: boolean;
  /** Đúng thao tác khôi phục đang chạy: chỉ khi đó nút hiện "Đang khôi phục…" (khoá vì thao tác khác thì giữ chữ cũ). */
  restoring: boolean;
  /** Báo trang vừa hiện; trả trang hiện lần trước (để biết có phải đổi trang không). */
  onShown: (pageNumber: number) => number | null;
  onRestore: () => void;
}

/**
 * Vùng xem một trang bỏ mềm: lý do máy ẩn (từ `role_evidence`, không suy luật), trang liên quan, văn bản đọc được ở dạng
 * chỉ đọc và nút "Khôi phục trang". KHÔNG có nút sửa dòng hay xác nhận (§5.7: trang bỏ mềm không sửa, không xác nhận
 * được cho tới khi khôi phục). Trang mới hiện thì focus vào tiêu đề, như vùng xem lại thường.
 */
export function DroppedPagePanel({
  page,
  documentId,
  subjectCode,
  hrefFor,
  busy,
  restoring,
  onShown,
  onRestore,
}: DroppedPagePanelProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  const reason = droppedReasonLabel(page.pageRole, page.roleEvidence?.rule ?? null);
  const related = relatedPageLinks(page.roleEvidence?.relatedPages ?? [], documentId);

  useEffect(() => {
    const previous = onShown(page.pageNumber);
    const active = window.document.activeElement;
    const where: ActiveOnShow = active === null || active === window.document.body ? "none" : "other";
    if (focusOnShow({ pageChanged: previous !== page.pageNumber, active: where }) === "heading") {
      heading.current?.focus();
    }
  }, [onShown, page.pageNumber]);

  return (
    <section className={vault.panel} aria-labelledby={`dropped-${page.pageNumber}-title`}>
      <div className={vault.panelHeader}>
        <h2 id={`dropped-${page.pageNumber}-title`} ref={heading} tabIndex={-1}>
          Trang {page.pageNumber}
        </h2>
        <span className={vault.badge} data-tone="neutral">
          Đã ẩn tự động
        </span>
      </div>
      <p className={styles.reason}>{reason}</p>
      {related.length > 0 && (
        <div>
          <p className={vault.hint} id={`dropped-${page.pageNumber}-related`}>
            Trang liên quan:
          </p>
          <ul className={styles.relatedList} aria-labelledby={`dropped-${page.pageNumber}-related`}>
            {related.map((item) => (
              <li key={`${item.documentId}:${item.pageNumber}`}>
                <Link href={hrefFor({ subject: subjectCode, doc: item.documentId, page: item.pageNumber })}>
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className={vault.hint}>
        Trang này chưa vào kho tri thức và không cần xác nhận. Nếu máy ẩn nhầm, bấm “Khôi phục trang”: trang trở lại như
        mọi trang khác, bạn xem lại rồi xác nhận thì mới vào kho.
      </p>
      <div className={vault.actions}>
        <button type="button" className={vault.primary} onClick={onRestore} disabled={busy}>
          {restoring ? "Đang khôi phục…" : "Khôi phục trang"}
        </button>
      </div>
      {page.lines.length > 0 && (
        <>
          <p className={vault.hint}>Văn bản đọc được của trang (chỉ để xem, chưa vào kho):</p>
          <ol className={styles.dimmedLines} aria-label="Các dòng của trang đã ẩn">
            {page.lines.map((line) => (
              <li key={line.lineIndex} className={styles.dimmedLine}>
                {line.text === "" ? "(dòng trống)" : line.text}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
