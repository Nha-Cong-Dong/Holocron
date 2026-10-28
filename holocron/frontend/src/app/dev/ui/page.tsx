import dynamic from "next/dynamic";

import { USE_MOCKS } from "@/lib/config";

// Bộ sưu tập thành phần chỉ nạp khi bật dữ liệu mẫu (môi trường phát triển); bản phát hành chỉ có dòng chữ bên dưới.
const UiGallery = dynamic(() => import("@/components/dev/UiGallery").then((module) => module.UiGallery));

/** Trang dev `/dev/ui`: mọi thành phần dùng chung ở mọi trạng thái. Không có link nào trỏ tới trang này. */
export default function DevUiPage() {
  if (!USE_MOCKS) return <p>Chỉ dùng khi phát triển.</p>;
  return <UiGallery />;
}
