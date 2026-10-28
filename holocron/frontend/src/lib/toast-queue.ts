/**
 * Hàng đợi thông báo nhanh (toast) dạng reducer thuần. `ToastProvider` giữ state này và hẹn giờ; module này chỉ quyết
 * định danh sách: thêm, bắt đầu rời đi, gỡ hẳn.
 */

/** Thời gian một thông báo nằm trên màn trước khi tự rời đi. */
export const TOAST_DURATION_MS = 5000;

/** Số thông báo hiện cùng lúc tối đa; thêm cái mới khi đã đủ thì cái cũ nhất bắt đầu rời đi. */
export const MAX_VISIBLE_TOASTS = 3;

export interface ToastItem {
  id: number;
  message: string;
  /** Đang chạy animation rời đi; gỡ hẳn khi animation xong (hoặc hết giờ dự phòng). */
  leaving: boolean;
}

export interface ToastQueue {
  items: readonly ToastItem[];
  nextId: number;
}

export const EMPTY_TOAST_QUEUE: ToastQueue = { items: [], nextId: 1 };

export type ToastAction =
  | { type: "push"; message: string }
  | { type: "leave"; id: number }
  | { type: "remove"; id: number };

function markLeaving(items: readonly ToastItem[], id: number): readonly ToastItem[] {
  return items.map((item) => (item.id === id && !item.leaving ? { ...item, leaving: true } : item));
}

export function toastReducer(state: ToastQueue, action: ToastAction): ToastQueue {
  switch (action.type) {
    case "push": {
      const message = action.message.trim();
      if (message === "") return state;
      // Cùng một câu đang hiện thì không chồng thêm bản thứ hai.
      if (state.items.some((item) => !item.leaving && item.message === message)) return state;
      let items: readonly ToastItem[] = [...state.items, { id: state.nextId, message, leaving: false }];
      const showing = items.filter((item) => !item.leaving);
      const overflow = showing.length - MAX_VISIBLE_TOASTS;
      for (const item of showing.slice(0, Math.max(overflow, 0))) items = markLeaving(items, item.id);
      return { items, nextId: state.nextId + 1 };
    }
    case "leave": {
      if (!state.items.some((item) => item.id === action.id && !item.leaving)) return state;
      return { ...state, items: markLeaving(state.items, action.id) };
    }
    case "remove": {
      if (!state.items.some((item) => item.id === action.id)) return state;
      return { ...state, items: state.items.filter((item) => item.id !== action.id) };
    }
  }
}
