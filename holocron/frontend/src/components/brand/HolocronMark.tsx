// Logo phẳng (thanh trên, icon nhỏ, chỉ báo đang tải). Màu lấy từ --cube-* trong tokens.css.
// spinning: xoay từng nấc 120° (chỉ báo đang xử lý, class .m-cube-spin trong motion.css).
// turns: số lần đã bấm; mỗi lần xoay thêm 120° (dùng cho logo thanh trên).
export function HolocronMark({
  size = 30,
  spinning = false,
  turns = 0,
  title,
}: {
  size?: number;
  spinning?: boolean;
  turns?: number;
  title?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={spinning ? "m-cube-spin" : undefined}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <g
        style={{
          transformBox: "fill-box",
          transformOrigin: "center",
          transform: spinning ? undefined : `rotate(${turns * 120}deg)`,
          transition: "transform 560ms var(--ease-spring)",
        }}
      >
        <polygon
          points="32,7 54,19.5 32,32 10,19.5"
          fill="var(--cube-top)"
          stroke="var(--bg)"
          strokeWidth={2.5}
          strokeLinejoin="round"
        />
        <polygon
          points="10,19.5 32,32 32,57 10,44.5"
          fill="var(--cube-left)"
          stroke="var(--bg)"
          strokeWidth={2.5}
          strokeLinejoin="round"
        />
        <polygon
          points="32,32 54,19.5 54,44.5 32,57"
          fill="var(--cube-right)"
          stroke="var(--bg)"
          strokeWidth={2.5}
          strokeLinejoin="round"
        />
        <polygon points="32,13.5 43,19.5 32,25.5 21,19.5" fill="var(--cube-core)" />
      </g>
    </svg>
  );
}
