/**
 * Object do vỏ desktop chèn vào trang lúc chạy (kế hoạch UI mục 4.3): `{ apiBase, token }`.
 * Khai báo là `unknown` để mọi chỗ đọc phải đi qua type guard `parseInjectedRuntime` ở `lib/config.ts`.
 */
export {};

declare global {
  interface Window {
    __HOLOCRON_RUNTIME__?: unknown;
  }
}
