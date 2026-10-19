import { UploadForm } from "@/components/ingestion/UploadForm";

export default function HomePage() {
  return (
    <main>
      <h1>Holocron — Ingestion Gate</h1>
      <p>
        Tải ảnh đề thi lên. Hệ thống đọc bảng hai lượt độc lập; ô nào hai lượt đọc lệch nhau hoặc độ tin cậy thấp sẽ
        được tô đỏ để bạn xác nhận trước khi sandbox tính toán.
      </p>
      <UploadForm />
    </main>
  );
}
