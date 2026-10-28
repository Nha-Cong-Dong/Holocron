import { Suspense } from "react";

import { Loading } from "@/components/common/LoadState";
import { SubjectPage } from "@/components/subject/SubjectPage";

export default function SubjectRoute() {
  return (
    <Suspense fallback={<Loading />}>
      <SubjectPage />
    </Suspense>
  );
}
