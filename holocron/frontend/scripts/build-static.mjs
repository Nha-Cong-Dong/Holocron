// Dựng site tĩnh (`output: "export"`) cho bản đóng gói; chạy được trên Windows và Linux, không thêm dependency.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");

const env = {
  ...process.env,
  HOLOCRON_STATIC_EXPORT: "1",
  NEXT_PUBLIC_HOLOCRON_API_BASE: "/",
  // Bản phát hành không mang dữ liệu mẫu, kể cả khi .env.local bật mock.
  NEXT_PUBLIC_HOLOCRON_MOCKS: "",
};

const result = spawnSync(process.execPath, [nextBin, "build"], { stdio: "inherit", env });

if (result.error) {
  process.stderr.write(`Không chạy được next build: ${result.error.message}\n`);
  process.exit(1);
}
if (result.signal) {
  process.stderr.write(`next build bị dừng bởi tín hiệu ${result.signal}\n`);
  process.exit(1);
}
process.exit(result.status ?? 1);
