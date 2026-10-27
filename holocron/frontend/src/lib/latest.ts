/**
 * Bộ đếm lượt gọi: chỉ kết quả của lượt gọi mới nhất được áp vào giao diện. Lượt cũ trả về muộn (ví dụ người dùng bấm
 * "Kiểm tra lại" hai lần, lần đầu trả sau lần hai) bị bỏ qua, để dữ liệu cũ không ghi đè dữ liệu mới.
 */
export interface LatestCounter {
  /** Bắt đầu một lượt mới; trả mã của lượt này. Mọi lượt trước đó trở thành cũ. */
  begin: () => number;
  /** Lượt `ticket` còn là lượt mới nhất hay không. */
  isLatest: (ticket: number) => boolean;
  /** Bỏ mọi lượt đang chờ (ví dụ khi component bị gỡ). */
  invalidate: () => void;
}

export function createLatestCounter(): LatestCounter {
  let current = 0;
  return {
    begin: () => {
      current += 1;
      return current;
    },
    isLatest: (ticket) => ticket === current,
    invalidate: () => {
      current += 1;
    },
  };
}
