import { Suspense } from "react";

import { LegacyRedirect, LegacyRedirectWaiting } from "@/components/shell/LegacyRedirect";

/** Route cũ: chuyển hướng sang tab tương ứng của trang môn (`lib/legacy-routes.ts`). */
export default function PracticePage() {
  return (
    <Suspense fallback={<LegacyRedirectWaiting />}>
      <LegacyRedirect path="/practice" />
    </Suspense>
  );
}
