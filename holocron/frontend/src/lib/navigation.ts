/**
 * Bảng route cũ (`NAV_ITEMS`), tiêu đề trang và tiêu đề breadcrumb của thanh trên, bộ lọc của hộp lệnh `Ctrl+K`. ID
 * của bản ghi đi qua query string, không dùng route động, để giữ được static export (kế hoạch UI 4.3). `NAV_ITEMS` giữ
 * nguyên cho các route cũ; điều hướng chính theo luồng mới không dùng bảng này.
 */

export interface NavItem {
  href: string;
  label: string;
  /** Chữ viết tắt (từ thời thanh bên; giữ cho bảng route cũ). */
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

/**
 * Tiêu đề của các trang theo luồng mới (thanh trên). Không nằm trong `NAV_ITEMS` vì không phải mục điều hướng cố định:
 * trang môn mở từ thẻ môn ở trang chủ, Cài đặt mở từ nút trên thanh trên.
 */
const PAGE_TITLES: Readonly<Record<string, string>> = {
  "/subject": "Trang môn",
  "/settings": "Cài đặt",
};

export function titleForPath(pathname: string): string {
  const path = trimTrailingSlash(pathname);
  return PAGE_TITLES[path] ?? navItemForPath(path)?.label ?? "Holocron";
}

/**
 * Tiêu đề mục cuối của breadcrumb theo chữ của luồng mới (design-handoff/README.md mục 7): route cũ không lộ "Vault"
 * hay "Cặp khái niệm". Không đổi `NAV_ITEMS`; đường dẫn không có trong bảng dùng `titleForPath`.
 */
const CRUMB_TITLES: Readonly<Record<string, string>> = {
  "/vault": "Tài liệu",
  "/pairs": "Khái niệm",
  "/practice": "Ôn tập",
  "/ingestion": "Nhập đề thi cũ",
  "/twins": "Câu hỏi song sinh",
};

export function crumbTitleForPath(pathname: string): string {
  const path = trimTrailingSlash(pathname);
  return CRUMB_TITLES[path] ?? titleForPath(path);
}

/**
 * Trang tự render `<h1>` của mình (trang chủ: "Bầu trời của bạn" hoặc lời chào; trang môn: tên môn; Cài đặt): khung
 * không render thêm.
 */
const PAGES_WITH_OWN_HEADING: ReadonlySet<string> = new Set(["/", "/subject", "/settings"]);

/**
 * Chữ của `<h1>` do khung render để mỗi route có đúng một `<h1>`: `null` khi trang tự có `<h1>` (kể cả trang chủ);
 * route cũ và đường dẫn khác dùng tiêu đề breadcrumb (`crumbTitleForPath`), hiện rõ.
 */
export function shellHeadingForPath(pathname: string): string | null {
  const path = trimTrailingSlash(pathname);
  if (PAGES_WITH_OWN_HEADING.has(path)) return null;
  return crumbTitleForPath(path);
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
