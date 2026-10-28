"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { SkyView } from "@/components/sky/SkyView";
import { Button, Card, Progress, Skeleton } from "@/components/ui";
import type { HomeCard } from "@/lib/home-cards";
import { buildSkyLayout, MINI_SKY } from "@/lib/sky";
import { subjectHref } from "@/lib/subject-route";

import styles from "./SubjectCard.module.css";

export interface SubjectCardProps {
  card: HomeCard;
  /** Tải lại số liệu của riêng thẻ này. */
  onRetry: () => void;
}

/** Bầu trời mini của thẻ khi đã có số liệu. */
function MiniSky({ card }: { card: HomeCard }) {
  const pairs = card.stats.kind === "ready" ? card.stats.pairs : null;
  const layout = useMemo(
    () => (pairs === null ? null : buildSkyLayout(card.name, pairs, MINI_SKY)),
    [card.name, pairs],
  );
  if (layout === null) return null;
  return <SkyView size="mini" status="ready" layout={layout} />;
}

/**
 * Thẻ môn ở trang chủ (design-handoff/README.md màn 1): bầu trời mini, tên môn, chỉ số thật backend đã trả (số tài liệu,
 * số khái niệm đã nhận, lần cập nhật), dòng tiến độ của tài liệu đang đọc, một nút đi tiếp. Đang tải: khung xương trong
 * thẻ; lỗi: "Chưa tải được số liệu" và nút "Thử lại" của riêng thẻ, chỉ số hiện "—".
 */
export function SubjectCard({ card, onRetry }: SubjectCardProps) {
  const router = useRouter();
  const { stats } = card;
  const titleId = `subject-card-${card.code}`;

  return (
    <Card as="li" className={styles.card}>
      <article className={styles.body} aria-labelledby={titleId}>
        {stats.kind === "loading" && <Skeleton height={72} className={styles.skySkeleton} />}
        {stats.kind === "error" && <div className={styles.skyPlaceholder} aria-hidden="true" />}
        {stats.kind === "ready" && <MiniSky card={card} />}

        <h2 id={titleId} className={styles.name}>
          <Link href={subjectHref(card.code, "documents")}>{card.name}</Link>
        </h2>

        {stats.kind === "loading" && (
          <div className={styles.stats} aria-busy="true">
            <span className="visually-hidden">Đang tải số liệu của môn {card.name}…</span>
            <Skeleton width="80%" height={14} />
            <Skeleton width="55%" height={14} />
          </div>
        )}
        {stats.kind === "error" && (
          <>
            <ul className={styles.stats}>
              <li>
                <b>—</b> tài liệu
              </li>
              <li>
                <b>—</b> khái niệm đã nhận
              </li>
            </ul>
            <div className={styles.cardError} role="status">
              <span>Chưa tải được số liệu</span>
              <Button variant="secondary" className={styles.retry} onClick={onRetry}>
                Thử lại
              </Button>
            </div>
          </>
        )}
        {stats.kind === "ready" && (
          <ul className={styles.stats}>
            <li>
              <b>{stats.documentCount}</b> tài liệu
            </li>
            <li>
              <b>{stats.confirmedCount}</b> khái niệm đã nhận
            </li>
          </ul>
        )}
        {card.updatedLabel !== null && <p className={styles.updated}>{card.updatedLabel}</p>}

        {stats.kind === "ready" && stats.reading.length > 0 && (
          <div className={styles.reading} role="status">
            {stats.reading.map((line) => (
              <div key={line.docId} className={styles.readingLine}>
                <p className={styles.readingText}>{line.text}</p>
                {line.progress !== null && (
                  <Progress
                    value={line.progress.pagesRead}
                    max={line.progress.pageCount}
                    unit="trang"
                    label={`Tiến độ đọc ${line.title}`}
                    active
                  />
                )}
              </div>
            ))}
            {stats.moreReading > 0 && (
              <p className={styles.readingText}>Đang đọc thêm {stats.moreReading} tài liệu khác</p>
            )}
          </div>
        )}

        <div className={styles.actions}>
          {card.primary ? (
            <Button variant="primary" aria-label={card.action.ariaLabel} onClick={() => router.push(card.action.href)}>
              {card.action.label}
            </Button>
          ) : (
            <Button
              variant="secondary"
              arrow
              aria-label={card.action.ariaLabel}
              onClick={() => router.push(card.action.href)}
            >
              {card.action.label}
            </Button>
          )}
        </div>
      </article>
    </Card>
  );
}
