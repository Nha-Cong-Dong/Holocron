---
name: holocron-ui
description: Quy tắc giao diện của Holocron (frontend Next.js). Nạp khi thiết kế, viết hoặc review màn hình, component hay CSS trong holocron/frontend/, kể cả khi giao brief cho frontend-impl hoặc review nhánh frontend.
---
# Quy tắc giao diện Holocron

Nguồn: `.claude/CLAUDE.md` §2.1, §2.3, §4.2, §4.3; kế hoạch chi tiết ở `docs/frontend/ui-plan.md` nếu có. Khi hai nguồn lệch nhau, CLAUDE.md thắng.

## Dữ liệu và bất biến
- Frontend chỉ hiển thị số do backend trả; KHÔNG tự tính điểm, tự chấm hay tự sinh đáp án (I1). Đúng/sai MCQ chỉ là so khoá đã chọn với `correct_key` backend trả.
- Render theo trường phân biệt của discriminated union (ví dụ `kind`, `question_kind`, `format`). Không component riêng cho một thuật toán hay một môn; không rẽ nhánh theo tên môn, chương, topic (§1).
- Issue BLOCKING của Ingestion Gate hiển thị rõ; còn BLOCKING thì không hiện đề hay đáp án sinh ra (I5). Nội dung chưa duyệt có nhãn "chưa duyệt".
- Mọi kiến thức hiển thị (định nghĩa, câu hỏi, đáp án) kèm trích dẫn nguyên văn và số trang (I3). Không trích dẫn được thì không hiện như kiến thức đã kiểm chứng.
- Kiểu dữ liệu khớp 1:1 với Pydantic trong `src/types/holocron.ts` (§2.3); kiểu của contract chưa có ở backend để riêng, không trộn vào file đó.

## Kỹ thuật
- Mọi lời gọi backend qua `src/lib/api.ts`; component không gọi `fetch`. Ép kiểu `as T` chỉ ở chỗ parse JSON trong `lib/api.ts`.
- Component có state hoặc tương tác đánh dấu `"use client"`. Logic thuần (gom issue, điều kiện approve, reducer, định dạng) đặt ở `src/lib/` kèm test vitest.
- CSS Modules + biến màu ở `app/globals.css`, có dark mode theo hệ điều hành; tôn trọng `prefers-reduced-motion`.
- Không `any`, `@ts-ignore`, `@ts-expect-error`, `as unknown as`, `eslint-disable`, `biome-ignore` (§4.2, §7).
- Chuỗi giao diện cố định bằng Tiếng Việt; định danh bằng Tiếng Anh (§4.3). Không lộ thuật ngữ backend (mã HTTP, tên field, enum thô) cho người dùng.

## Checklist review UI
- Tương phản chữ ≥ 4.5:1 ở cả sáng và tối; focus ring nhìn thấy được.
- Điều hướng hoàn toàn bằng bàn phím; hộp thoại đóng bằng Esc và trả focus về chỗ cũ; phím tắt không chạy khi đang gõ hoặc khi có hộp thoại.
- Mọi màn có trạng thái rỗng (chỉ bước tiếp theo), đang tải và lỗi (lỗi có hành động làm đúng điều nó nói).
- Màn hẹp (khoảng 1024px) vẫn dùng được, không cuộn ngang.
- Dữ liệu backend lạ hoặc sai dạng không làm sập trang (kiểm ở ranh giới trong `lib/`).
