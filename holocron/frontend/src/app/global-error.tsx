"use client";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { renderError } from "@/lib/errors";

import "./globals.css";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Ranh giới lỗi ngoài cùng của App Router: dùng khi chính khung app (layout gốc) ném lỗi, nên phải tự dựng `<html>` và
 * `<body>`. Không hiển thị `error.message` vì có thể chứa chi tiết kỹ thuật.
 */
export default function GlobalError({ reset }: GlobalErrorProps) {
  return (
    <html lang="vi">
      <body>
        <main className="global-error">
          <h1>Holocron không hiển thị được</h1>
          <ErrorNotice error={renderError()} onRetry={reset} />
        </main>
      </body>
    </html>
  );
}
