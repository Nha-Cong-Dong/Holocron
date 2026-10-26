import { Suspense } from "react";

import { Loading } from "@/components/common/LoadState";
import { VaultView } from "@/components/vault/VaultView";

export default function VaultPage() {
  return (
    <Suspense fallback={<Loading />}>
      <VaultView />
    </Suspense>
  );
}
