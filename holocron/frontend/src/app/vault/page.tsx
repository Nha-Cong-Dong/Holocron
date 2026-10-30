import { Suspense } from "react";

import { LegacyRedirect, LegacyRedirectWaiting } from "@/components/shell/LegacyRedirect";

/** Route cũ: chuyển hướng sang tab tương ứng của trang môn (`lib/legacy-routes.ts`). */
export default function VaultPage() {
  return (
    <Suspense fallback={<LegacyRedirectWaiting />}>
      <LegacyRedirect path="/vault" />
    </Suspense>
  );
}
