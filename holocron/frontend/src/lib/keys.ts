/**
 * Gắn key React ổn định cho danh sách hiển thị không có định danh riêng (ô bảng, artifact): key dựng từ nội dung,
 * phần tử trùng nội dung được thêm hậu tố theo thứ tự xuất hiện để key không trùng (cùng cách với `keyedMessages`).
 */
export function withContentKeys<T>(items: readonly T[], contentOf: (item: T) => string): { key: string; item: T }[] {
  const seen = new Map<string, number>();
  return items.map((item) => {
    const content = contentOf(item);
    const count = seen.get(content) ?? 0;
    seen.set(content, count + 1);
    return { key: count === 0 ? content : `${content}#${count}`, item };
  });
}
