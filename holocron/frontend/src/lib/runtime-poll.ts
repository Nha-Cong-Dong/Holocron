/**
 * Vòng hỏi lại `GET /v1/runtime` khi backend còn kiểm OCR nền (`local_check_pending`), logic thuần để test.
 * - còn pending: hỏi tiếp (khoảng chờ và chống phản hồi cũ do `createPoller`);
 * - hết pending: dừng;
 * - một lần lỗi mạng: im lặng, giữ nguyên runtime đang hiển thị;
 * - lỗi liên tiếp đủ ngưỡng: dừng và báo lỗi để giao diện hiện nút "Kiểm tra lại".
 */

import type { UiError } from "@/lib/errors";
import { createPoller, type Poller, type PollOptions } from "@/lib/poll";
import { hasPendingLocalCheck, type RuntimeAction, type RuntimeView } from "@/lib/runtime";

export interface RuntimePollDeps {
  load: () => Promise<RuntimeView>;
  dispatch: (action: RuntimeAction) => void;
  toUiError: (error: unknown) => UiError;
  schedule?: PollOptions<RuntimeView>["schedule"];
  maxConsecutiveErrors?: number;
}

export function createRuntimePoller(deps: RuntimePollDeps): Poller {
  return createPoller<RuntimeView>({
    load: deps.load,
    shouldContinue: (view) => hasPendingLocalCheck(view),
    onValue: (view) => deps.dispatch({ type: "load_succeeded", runtime: view }),
    onError: (error, gaveUp) => {
      if (gaveUp) deps.dispatch({ type: "load_failed", error: deps.toUiError(error) });
    },
    schedule: deps.schedule,
    maxConsecutiveErrors: deps.maxConsecutiveErrors,
  });
}
