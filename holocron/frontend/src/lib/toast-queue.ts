/**
 * Hàng đợi thông báo nhanh (toast) dạng reducer thuần. `ToastProvider` giữ state này và hẹn giờ; module này quyết định
 * danh sách (thêm, bắt đầu rời đi, gỡ hẳn) và thời gian còn lại của từng thông báo. Rê chuột hoặc focus vào vùng
 * thông báo thì tạm dừng đếm giờ (WCAG 2.2.1), rời ra thì đếm tiếp phần còn lại. Thời điểm `now` (ms) do nơi gọi truyền
 * vào để reducer giữ thuần.
 */

/** Thời gian một thông báo nằm trên màn trước khi tự rời đi (không tính lúc tạm dừng). */
export const TOAST_DURATION_MS = 5000;

/** Số thông báo hiện cùng lúc tối đa; thêm cái mới khi đã đủ thì cái cũ nhất bắt đầu rời đi. */
export const MAX_VISIBLE_TOASTS = 3;

export interface ToastItem {
  id: number;
  message: string;
  /** Đang chạy animation rời đi; gỡ hẳn khi animation xong (hoặc hết giờ dự phòng). */
  leaving: boolean;
  /** Thời gian hiện còn lại tính từ `startedAt` (hoặc từ lúc đếm tiếp khi đang tạm dừng). */
  remainingMs: number;
  /** Lúc bắt đầu đếm lượt hiện tại; `null` khi đang tạm dừng. */
  startedAt: number | null;
}

export interface ToastQueue {
  items: readonly ToastItem[];
  nextId: number;
  /** Đang tạm dừng đếm giờ (người dùng rê chuột hoặc focus vào vùng thông báo). */
  paused: boolean;
}

export const EMPTY_TOAST_QUEUE: ToastQueue = { items: [], nextId: 1, paused: false };

export type ToastAction =
  | { type: "push"; message: string; now: number }
  | { type: "leave"; id: number }
  | { type: "remove"; id: number }
  | { type: "pause"; now: number }
  | { type: "resume"; now: number };

function markLeaving(items: readonly ToastItem[], id: number): readonly ToastItem[] {
  return items.map((item) => (item.id === id && !item.leaving ? { ...item, leaving: true } : item));
}

/** Lúc thông báo phải tự rời đi; `null` khi đang tạm dừng hoặc đã rời đi. */
export function toastDeadline(item: ToastItem): number | null {
  if (item.leaving || item.startedAt === null) return null;
  return item.startedAt + item.remainingMs;
}

export function toastReducer(state: ToastQueue, action: ToastAction): ToastQueue {
  switch (action.type) {
    case "push": {
      const message = action.message.trim();
      if (message === "") return state;
      // Cùng một câu đang hiện thì không chồng thêm bản thứ hai.
      if (state.items.some((item) => !item.leaving && item.message === message)) return state;
      const added: ToastItem = {
        id: state.nextId,
        message,
        leaving: false,
        remainingMs: TOAST_DURATION_MS,
        startedAt: state.paused ? null : action.now,
      };
      let items: readonly ToastItem[] = [...state.items, added];
      const showing = items.filter((item) => !item.leaving);
      const overflow = showing.length - MAX_VISIBLE_TOASTS;
      for (const item of showing.slice(0, Math.max(overflow, 0))) items = markLeaving(items, item.id);
      return { ...state, items, nextId: state.nextId + 1 };
    }
    case "leave": {
      if (!state.items.some((item) => item.id === action.id && !item.leaving)) return state;
      return { ...state, items: markLeaving(state.items, action.id) };
    }
    case "remove": {
      if (!state.items.some((item) => item.id === action.id)) return state;
      return { ...state, items: state.items.filter((item) => item.id !== action.id) };
    }
    case "pause": {
      if (state.paused) return state;
      const items = state.items.map((item) => {
        if (item.leaving || item.startedAt === null) return item;
        const elapsed = Math.max(action.now - item.startedAt, 0);
        return { ...item, remainingMs: Math.max(item.remainingMs - elapsed, 0), startedAt: null };
      });
      return { ...state, items, paused: true };
    }
    case "resume": {
      if (!state.paused) return state;
      const items = state.items.map((item) =>
        item.leaving || item.startedAt !== null ? item : { ...item, startedAt: action.now },
      );
      return { ...state, items, paused: false };
    }
  }
}
