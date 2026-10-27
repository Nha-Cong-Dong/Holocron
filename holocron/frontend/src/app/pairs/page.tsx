import { Suspense } from "react";

import { Loading } from "@/components/common/LoadState";
import { PairsView } from "@/components/pairs/PairsView";

export default function PairsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <PairsView />
    </Suspense>
  );
}
