"use client";

import { ErrorNotice } from "@/components/common/ErrorNotice";
import { renderError } from "@/lib/errors";

interface ErrorPageProps {
  error: Error & { digest?: string };
  reset: () => void;
}

/**
 * Ranh giới lỗi của App Router: một màn hình ném lỗi khi render thì chỉ vùng nội dung hiện thông báo tiếng Việt, khung
 * app (thanh bên, thanh trạng thái) vẫn dùng được. Không hiển thị `error.message` vì có thể chứa chi tiết kỹ thuật.
 */
export default function ErrorPage({ reset }: ErrorPageProps) {
  return (
    <section aria-labelledby="render-error-title">
      <h2 id="render-error-title">Không hiển thị được màn hình này</h2>
      <ErrorNotice error={renderError()} onAction={reset} />
    </section>
  );
}
