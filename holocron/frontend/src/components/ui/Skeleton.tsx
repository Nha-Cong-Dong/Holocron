import type { CSSProperties } from "react";

export interface SkeletonProps {
  /** Độ rộng (số = px), mặc định đầy dòng. */
  width?: number | string;
  /** Độ cao (số = px), mặc định một dòng chữ. */
  height?: number | string;
  /** Hình tròn (ảnh đại diện, icon) thay cho thanh bo góc. */
  circle?: boolean;
  className?: string;
}

/**
 * Khối giữ chỗ khi đang tải (`.m-skeleton`, ánh sáng quét qua; giảm chuyển động thì đứng yên). Chỉ để trang trí
 * (`aria-hidden`): vùng đang tải tự báo bằng `aria-busy` hoặc chữ "Đang tải…".
 */
export function Skeleton({ width = "100%", height = 16, circle = false, className }: SkeletonProps) {
  const style: CSSProperties = { display: "block", width, height };
  if (circle) style.borderRadius = "50%";
  return (
    <span
      className={["m-skeleton", className ?? ""].filter((part) => part !== "").join(" ")}
      style={style}
      aria-hidden="true"
    />
  );
}
