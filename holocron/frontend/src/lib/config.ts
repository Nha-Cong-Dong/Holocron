/**
 * Cấu hình chạy của frontend. Đây là chỗ DUY NHẤT đọc `process.env` và `window.__HOLOCRON_RUNTIME__`
 * (kế hoạch UI mục 4.3). Code tương thích static export: không đọc `window` lúc import hay lúc render,
 * chỉ đọc khi một lời gọi API thực sự chạy (trong `useEffect` hoặc handler).
 *
 * Thứ tự chọn base URL của API:
 * 1. object do vỏ desktop chèn lúc chạy, `window.__HOLOCRON_RUNTIME__ = { apiBase, token }`, kiểm bằng type guard;
 * 2. biến build `NEXT_PUBLIC_HOLOCRON_API_BASE`;
 * 3. mặc định `/api/holocron` (đi qua rewrite của `next dev`).
 */

export const DEFAULT_API_BASE = "/api/holocron";

/** Tên header token đã duyệt ở spec §13.1; chỉ gắn khi vỏ desktop cấp token. */
export const TOKEN_HEADER = "X-Holocron-Token";

// Next chỉ chép giá trị `NEXT_PUBLIC_*` vào bundle khi biểu thức viết nguyên văn `process.env.TÊN`.
const BUILD_API_BASE: string | undefined = process.env.NEXT_PUBLIC_HOLOCRON_API_BASE;
const BUILD_MOCKS_FLAG: string | undefined = process.env.NEXT_PUBLIC_HOLOCRON_MOCKS;

/** Đang chạy `next dev`: chỉ khi đó giao diện mới hiện lệnh dành cho người phát triển (ví dụ lệnh chạy backend). */
export const IS_DEVELOPMENT: boolean = process.env.NODE_ENV === "development";

/** Object vỏ desktop chèn vào trang, sau khi đã kiểm dạng. */
export interface InjectedRuntime {
  apiBase: string;
  token: string | null;
}

/** Cờ build bật dữ liệu mẫu cho endpoint backend chưa có (kế hoạch UI mục 3.2). */
export function parseMockFlag(value: string | undefined): boolean {
  return value === "1" || value === "true";
}

export const USE_MOCKS: boolean = parseMockFlag(BUILD_MOCKS_FLAG);

const ABSOLUTE_HTTP_URL = /^https?:\/\/[^/?#]+/i;
const HEADER_SAFE_TOKEN = /^[\x21-\x7e]{1,512}$/;

/**
 * Chuẩn hoá base URL: nhận URL tuyệt đối `http(s)://…` hoặc đường dẫn bắt đầu bằng một dấu `/`; bỏ `/` cuối.
 * Trả `null` khi giá trị không dùng được (rỗng, có khoảng trắng, đường dẫn tương đối, `//host`, scheme khác, có query
 * `?…` hoặc fragment `#…`, vì đường dẫn endpoint được nối ngay sau base).
 *
 * Cũng từ chối dấu `\` (trình duyệt coi `\` như `/`, nên `http://a\b` trỏ tới máy khác với điều người đọc thấy) và
 * dấu `@` (phần `user:pass@` trước tên máy là thông tin xác thực; giữ lại thì nó bị in ra giao diện và có thể che
 * tên máy thật, ví dụ `http://127.0.0.1@may-khac`).
 */
export function normalizeApiBase(value: string): string | null {
  const trimmed = value.trim();
  if (trimmed === "" || /[\s?#\\@]/.test(trimmed)) return null;
  const isAbsolute = ABSOLUTE_HTTP_URL.test(trimmed);
  const isRootPath = trimmed.startsWith("/") && !trimmed.startsWith("//");
  if (!isAbsolute && !isRootPath) return null;
  return trimmed.replace(/\/+$/, "");
}

/** Type guard cho object chèn lúc chạy. Sai dạng ở bất kỳ field nào thì bỏ cả object. */
export function parseInjectedRuntime(value: unknown): InjectedRuntime | null {
  if (typeof value !== "object" || value === null || !("apiBase" in value)) return null;
  const rawBase = value.apiBase;
  if (typeof rawBase !== "string") return null;
  const apiBase = normalizeApiBase(rawBase);
  if (apiBase === null) return null;

  const rawToken = "token" in value ? value.token : null;
  if (rawToken === null || rawToken === undefined) return { apiBase, token: null };
  if (typeof rawToken !== "string" || !HEADER_SAFE_TOKEN.test(rawToken)) return null;
  return { apiBase, token: rawToken };
}

export function resolveApiBase(injected: InjectedRuntime | null, buildBase: string | undefined): string {
  if (injected !== null) return injected.apiBase;
  if (buildBase !== undefined) {
    const normalized = normalizeApiBase(buildBase);
    if (normalized !== null) return normalized;
  }
  return DEFAULT_API_BASE;
}

export function resolveAuthHeaders(injected: InjectedRuntime | null): Record<string, string> {
  if (injected === null || injected.token === null) return {};
  return { [TOKEN_HEADER]: injected.token };
}

function readInjectedRuntime(): InjectedRuntime | null {
  if (typeof window === "undefined") return null;
  return parseInjectedRuntime(window.__HOLOCRON_RUNTIME__);
}

/** Base URL của API cho lời gọi đang chạy. */
export function apiBase(): string {
  return resolveApiBase(readInjectedRuntime(), BUILD_API_BASE);
}

/** Header xác thực cho lời gọi đang chạy; rỗng khi không chạy dưới vỏ desktop có token. */
export function authHeaders(): Record<string, string> {
  return resolveAuthHeaders(readInjectedRuntime());
}
