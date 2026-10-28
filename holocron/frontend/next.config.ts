import type { NextConfig } from "next";

/** Địa chỉ FastAPI cục bộ; trình duyệt chỉ gọi `/api/holocron/*` cùng origin nên không cần CORS. */
const backendUrl = process.env.HOLOCRON_API_URL ?? "http://127.0.0.1:8000";

/** Cờ chỉ dùng lúc build (không có tiền tố NEXT_PUBLIC_ nên không vào bundle); `scripts/build-static.mjs` đặt cờ này. */
const isStaticExport = process.env.HOLOCRON_STATIC_EXPORT === "1";

/** Bật dữ liệu mẫu (cùng quy ước với `parseMockFlag` ở `src/lib/config.ts`). */
const mockFlag = process.env.NEXT_PUBLIC_HOLOCRON_MOCKS;
const useMocks = mockFlag === "1" || mockFlag === "true";

/**
 * Trang chỉ dành cho người phát triển đặt tên `page.dev.tsx` (ví dụ `/dev/ui`): chỉ thành route khi bật dữ liệu mẫu.
 * Bản phát hành (`build:static` tắt dữ liệu mẫu) không có các trang đó trong `out/`.
 */
const pageExtensions = useMocks ? ["dev.tsx", "tsx", "ts", "jsx", "js"] : ["tsx", "ts", "jsx", "js"];

/** Site tĩnh do backend phục vụ (không có rewrites); mặc định giữ cấu hình dev/start như cũ. */
const nextConfig: NextConfig = isStaticExport
  ? { reactStrictMode: true, output: "export", trailingSlash: true, pageExtensions }
  : {
      reactStrictMode: true,
      pageExtensions,
      async rewrites() {
        return [{ source: "/api/holocron/:path*", destination: `${backendUrl}/:path*` }];
      },
    };

export default nextConfig;
