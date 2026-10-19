import type { NextConfig } from "next";

/** Địa chỉ FastAPI cục bộ; trình duyệt chỉ gọi `/api/holocron/*` cùng origin nên không cần CORS. */
const backendUrl = process.env.HOLOCRON_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/api/holocron/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
