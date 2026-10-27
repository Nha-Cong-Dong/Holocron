import { Suspense } from "react";

import { Loading } from "@/components/common/LoadState";
import { TwinsView } from "@/components/twins/TwinsView";

export default function TwinsPage() {
  return (
    <Suspense fallback={<Loading />}>
      <TwinsView />
    </Suspense>
  );
}
