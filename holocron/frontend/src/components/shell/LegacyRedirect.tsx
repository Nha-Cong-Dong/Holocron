"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { HolocronMark } from "@/components/brand/HolocronMark";
import { legacyRedirectTarget } from "@/lib/legacy-routes";
import { readRecentSubject } from "@/lib/recent-subject";
import { browserStorage } from "@/lib/theme";

/**
 * Khung chờ của trang chuyển hướng: khối Holocron xoay và một câu. Mang `data-legacy-redirect` để smoke test của bản
 * đóng gói nhận ra HTML dựng sẵn của route cũ là trang chuyển hướng (không còn màn cũ).
 */
export function LegacyRedirectWaiting() {
  return (
    <output data-legacy-redirect="" aria-live="polite">
      <HolocronMark size={28} spinning /> Đang mở trang mới…
    </output>
  );
}

/**
 * Route cũ (`/pairs`, `/practice`, `/vault`) chỉ còn chuyển hướng phía client sang tab tương ứng của trang môn
 * (`legacyRedirectTarget`), bằng `router.replace` để nút Back không quay lại trang chuyển hướng. Đọc
 * `useSearchParams()` nên phải nằm trong `<Suspense>` (fallback là `LegacyRedirectWaiting`).
 */
export function LegacyRedirect({ path }: { path: string }) {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    router.replace(legacyRedirectTarget(path, params, readRecentSubject(browserStorage())));
  }, [router, path, params]);

  return <LegacyRedirectWaiting />;
}
