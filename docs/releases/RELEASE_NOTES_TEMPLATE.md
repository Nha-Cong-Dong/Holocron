# Holocron vX.Y.Z[-pre] — YYYY-MM-DD

<!--
Phần thân (5 nhóm) được trích tự động từ CHANGELOG.md bằng `scripts/extract_release_notes.py`;
chỉ cần điền thêm các mục "Số liệu" và "Liên kết" sau khi release được tạo.
-->

## 🚀 Tính năng mới
- …

## 🛠️ Cải tiến & Tối ưu
- …

## 🐛 Sửa lỗi
- …

## 🔒 Bảo mật Sandbox
- …

## ⚠️ Thay đổi không tương thích
- …

## Số liệu (từ `bench.json` và đợt kiểm OCR/VLM)

| Chỉ số | Kết quả | Ngưỡng |
|---|---|---|
| Cold start backend tới `/docs` | … s | ≤ 5 s |
| Twin p95 (không tính LLM) | … s | ≤ 3 s |
| Gate PATCH p95 | … ms | ≤ 500 ms |
| Tỉ lệ seed được nhận | … % | ≥ 10 % |
| Sandbox thoát bất thường | …/200 | 0 |
| Tái lập I4 | … seed lệch | 0 |
| Dual-pass: số ảnh đề thật đã thử (kể cả ảnh mờ/ghi tay) | … | ≥ 5 (≥ 2 mờ/ghi tay) |
| Dual-pass: ô đọc đúng / tổng số ô | …/… | ghi rõ |
| Gate gắn cờ mọi ô sai (recall) | … % | 100 % |

## Liên kết
- Video demo: …
- Dữ liệu mẫu offline: …
