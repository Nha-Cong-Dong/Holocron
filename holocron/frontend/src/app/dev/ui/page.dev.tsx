import { UiGallery } from "@/components/dev/UiGallery";

/**
 * Trang dev `/dev/ui`: mọi thành phần dùng chung ở mọi trạng thái. Phần mở rộng `.dev.tsx` chỉ là trang khi bật dữ
 * liệu mẫu (`pageExtensions` trong `next.config.ts`), nên bản phát hành không có trang này. Không có link nào trỏ tới.
 */
export default function DevUiPage() {
  return <UiGallery />;
}
