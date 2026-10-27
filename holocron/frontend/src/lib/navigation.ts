/**
 * Bảng điều hướng dùng chung cho thanh bên, tiêu đề trang và hộp lệnh `Ctrl+K`. Mỗi giai đoạn F2–F5 thêm màn của
 * mình vào đây. ID của bản ghi đi qua query string, không dùng route động, để giữ được static export (kế hoạch UI 4.3).
 */

export interface NavItem {
  href: string;
  label: string;
  /** Chữ viết tắt khi thanh bên thu hẹp (dưới 1100 px). */
  short: string;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/", label: "Bắt đầu", short: "BĐ", description: "Khả năng của máy này và bước tiếp theo" },
  {
    href: "/vault",
    label: "Vault",
    short: "VT",
    description: "Môn học, nhập tài liệu PDF, xem lại trang và kho tri thức",
  },
  {
    href: "/pairs",
    label: "Cặp khái niệm",
    short: "CK",
    description: "Bôi định nghĩa trong văn bản của Vault, xác nhận hoặc từ chối cặp khái niệm",
  },
  {
    href: "/practice",
    label: "Luyện tập",
    short: "LT",
    description: "Tạo đề từ cặp khái niệm đã xác nhận và làm từng câu",
  },
  { href: "/ingestion", label: "Nhập đề thi", short: "ĐT", description: "Tải ảnh đề, kiểm duyệt bảng số liệu" },
  { href: "/twins", label: "Câu hỏi song sinh", short: "SS", description: "Sinh câu hỏi song sinh cho một bộ đề" },
];

/** Bỏ `/` cuối (export tĩnh có thể bật `trailingSlash`), giữ `/` cho trang gốc. */
function trimTrailingSlash(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, "");
  return trimmed === "" ? "/" : trimmed;
}

export function navItemForPath(pathname: string): NavItem | null {
  const path = trimTrailingSlash(pathname);
  return NAV_ITEMS.find((item) => item.href === path) ?? null;
}

export function titleForPath(pathname: string): string {
  return navItemForPath(pathname)?.label ?? "Holocron";
}

/** Chuẩn hoá để lọc: NFD, bỏ dấu, `đ` → `d`, chữ thường, gộp khoảng trắng. */
export function foldForSearch(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}+/gu, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

export interface Command {
  id: string;
  label: string;
  hint: string;
}

/** Lọc lệnh theo mọi từ của truy vấn (không phân biệt dấu, hoa thường); truy vấn rỗng giữ nguyên danh sách. */
export function filterCommands<T extends Command>(commands: readonly T[], query: string): T[] {
  const words = foldForSearch(query)
    .split(" ")
    .filter((word) => word !== "");
  if (words.length === 0) return [...commands];
  return commands.filter((command) => {
    const haystack = foldForSearch(`${command.label} ${command.hint}`);
    return words.every((word) => haystack.includes(word));
  });
}
