import styles from "./TesseractHelp.module.css";

/**
 * Hướng dẫn cài Tesseract và `vie.traineddata` (ADR-008 mục 8; spec §5.7): hiện ở màn Bắt đầu khi khả năng `ocr`
 * thiếu, và ở màn xem lại trang khi trang `failed` vì `OCR_ENGINE_MISSING`.
 */
export function TesseractHelp({ headingLevel = 3 }: { headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className={styles.help}>
      <Heading>Cài Tesseract để đọc trang scan</Heading>
      <ol>
        <li>Cài Tesseract OCR cho hệ điều hành của bạn.</li>
        <li>
          Thêm dữ liệu tiếng Việt <code>vie.traineddata</code> vào thư mục <code>tessdata</code> của Tesseract.
        </li>
        <li>
          Nếu Tesseract không nằm trong PATH, đặt <code>HOLOCRON_TESSERACT_CMD</code> (file chạy) hoặc{" "}
          <code>HOLOCRON_TESSDATA_DIR</code> cho backend, rồi khởi động lại backend.
        </li>
      </ol>
      <p className={styles.muted}>
        Chưa cài thì trang PDF có lớp chữ vẫn đọc được; trang scan sẽ báo lỗi cho tới khi cài xong.
      </p>
    </div>
  );
}
