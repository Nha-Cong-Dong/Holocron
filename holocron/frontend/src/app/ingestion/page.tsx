import { Suspense } from "react";

import { Loading } from "@/components/common/LoadState";
import { IngestionView } from "@/components/ingestion/IngestionView";

export default function IngestionPage() {
  return (
    <Suspense fallback={<Loading />}>
      <IngestionView />
    </Suspense>
  );
}
