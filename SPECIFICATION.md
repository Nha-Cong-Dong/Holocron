# Holocron — Technical Specification & PRD

| Trường | Giá trị |
|---|---|
| Trạng thái | **v0.7, bổ sung 2026-11-01: bóc tách theo khối (ADR-010)** (vẫn v0.7): contract khối, mục, sổ bảo toàn và ảnh cắt (§5.15), trường font và giao thức worker cho bộ dựng khối, `PageGeometry`, `layout_version` (§5.7), Vault theo khối (§5.8, §12.1, §12.3), migration 0006/0007 và chỉ mục FTS khối có điều kiện (§5.13), endpoint cây/khối/ảnh cắt/tra cứu khối (§6.10, §6.5), ảnh cắt (§13.1). Mỗi chỗ ghi "duyệt 2026-11-01 (ADR-010)" = thuộc phạm vi người dùng duyệt ở mục 3 của `.claude/state/briefs/hackathon-2026-11-01.md`; tên/ngưỡng do người viết đặt ghi "đề xuất suy ra". §0 không đổi; `cross_check.TOKEN_PATTERN`, `chunks_fts*`, `GET /v1/vault/search` không đổi. **v0.7, bổ sung 2026-10-29** (vẫn v0.7): §5.12/§11.4 thêm 3 khả năng ở trạng thái đề xuất, chờ duyệt; §6.4 `GET /v1/runtime` trả `capabilities` (12 phần tử, theo code đã triển khai). Nhánh `feat/api-capabilities-ui` bổ sung contract (duyệt 2026-10-29/30): `ocr` và `ocr_cross_check` đã triển khai (kiểm cục bộ một lần, chạy nền sau khi server mở cổng, kết quả lưu lại, không probe lại mỗi request); mã `secondary_ocr_unavailable` (2026-10-29) và `local_check_pending` (2026-10-30, chỉ áp cho hai khả năng OCR khi kiểm nền chưa xong); thứ tự xác định thư mục tessdata (2026-10-30, §5.12/§11.4): `HOLOCRON_TESSDATA_DIR`, rồi thư mục `--list-langs` (Tesseract 5.x), rồi đường lùi mặc định của bản cài (Tesseract 4.x); app không đọc `TESSDATA_PREFIX`. §0 không đổi. **v0.7, bổ sung 2026-10-30: điều kiện (d) `image_covers_page`** (vẫn v0.7): thêm `TEXT_FULL_IMAGE_MIN=0.90` và `TEXT_FULL_IMAGE_MAX_CHARS=200` vào luật chọn tầng (§5.7, §6.5, §11.2; ADR-008 mục 6, 12); người dùng duyệt 2026-10-30. **v0.7, bổ sung 2026-10-29: luật chọn tầng (ADR-008 mục 6, 12)** (vẫn v0.7): bộ nhập PDF chỉ OCR cả trang khi lớp chữ rỗng, lớp chữ rác (`text_score < TEXT_LAYER_MIN`) hoặc trang ảnh (`non_ws < TEXT_SPARSE_CHARS` VÀ `image_area_ratio ≥ TEXT_SPARSE_IMAGE_MIN`), §5.7, §6.5, §11.2; người dùng duyệt. **v0.7, bổ sung 2026-10-28 (môn học và blueprint lý thuyết, theo code đã triển khai)** (vẫn v0.7): ghi contract môn học và blueprint slot lý thuyết đã triển khai, mỗi chỗ sửa ghi "(duyệt 2026-10-28)": §6.8 môn học (câu 1) và `POST/GET /v1/blueprints` chỉ slot lý thuyết (câu 2; `item_ids` khác rỗng ⇒ 422, câu 15) đã triển khai; câu lý thuyết vào `TwinBatch` theo phương án (b) của câu 14 (union theo `question_kind`, không đổi `examples/`): `TheorySlot` (§5.9), `TheoryTwin` và `TwinBatch.twins` union, không có `TheoryTrace` (§5.11), seed R6 và dấu vân tay thay thế cho slot lý thuyết (§6.3). §4 bước 5 và §17 D8 sửa khớp; nợ ghi ở ADR-004 (ARCH-002…ARCH-008). Shape (a) cũ của §5.9/§5.11/§6.3 giữ nguyên dưới nhãn "mục tiêu sau 29/10 (nợ ADR-004)". `review_status = AUTO_PASS` của câu lý thuyết ghi theo code, duyệt 2026-10-28 (câu 24). §0 không đổi. **v0.7, bổ sung 2026-10-28** (vẫn v0.7): kiểm `Host` và token đã triển khai (PR #81) bằng middleware ASGI `LocalAccessMiddleware` chạy trước routing, bao cả file tĩnh của chế độ portable; sửa cho khớp code ở §6.0, §13.1, §17 D13; hành vi token sai dạng (fail-closed 403) ở §6.0, §11.2, §13.1 duyệt 2026-10-28 (câu 22) và đã triển khai ở PR sửa token (PR #97); **duyệt 2026-10-29 (câu 6), thay quyết định câu 21 (a) ngày 2026-10-28:** `HOLOCRON_REQUIRE_TOKEN` không đặt hoặc `""` ⇒ không yêu cầu token; có giá trị mà toàn khoảng trắng (mọi khoảng trắng Unicode) hoặc không hợp lệ ⇒ cấu hình hỏng, app từ chối khởi động (mã thoát theo đường chạy, §13.1), middleware vẫn 403 fail-closed như lớp thứ hai; `RuntimeInfo` thêm `app_version`, `commit`, `log_dir` (§5.12, §6.4, §14; duyệt 2026-10-28, quyết định người dùng câu 7; đã triển khai (nhánh feat/api-runtime-build-info, duyệt 2026-10-28)); §0 không đổi. **v0.7, bổ sung 2026-10-27** (vẫn v0.7): ghi log file của bản portable ở §14 (2026-10-28; đã triển khai, PR #80), kèm nợ PKG-003 ở ADR-004; ghi ngoại lệ GEOS LGPL-2.1 (liên kết động) cho bản zip portable đủ có RapidOCR (Q4, duyệt 2026-10-27; ADR-008). Thêm contract chế độ portable, backend phục vụ frontend tĩnh qua `HOLOCRON_WEB_DIR` (§6.0, §11.2): đã triển khai (#69, #71, #72); tên biến duyệt 2026-10-27 (Q8); Q1 chọn (a), chưa triển khai. Bỏ tên nhà cung cấp mặc định cũ khỏi spec (PR #61), mỗi chỗ sửa ghi "(duyệt 2026-10-27)" khi đổi hành vi: LLM chỉ bật khi đặt tường minh đủ `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`; thiếu thì khả năng cần LLM báo `llm_not_configured` với `detail_vi` cố định nêu đủ ba biến, endpoint cần LLM trả 409 `CapabilityError` và không gọi mạng (§11.2, §17 D12); ADR-006, ADR-007, ADR-008 thêm dòng "Sửa đổi 2026-10-27", trạng thái không đổi; §0 không đổi. **v0.7, bổ sung 2026-10-26 (đợt hai chiều, người dùng trả lời lúc ~13:40)** (vẫn v0.7): spec v0.7 là contract mục tiêu, không phải phạm vi MVP; phạm vi MVP-1 (26–29/10, chốt 2026-10-26) ở §1.5 và lịch mới ở §16 (hạn nộp hackathon Chủ nhật 8/11/2026). Ghi các quyết định câu 19, mỗi chỗ sửa ghi "(duyệt 2026-10-26)": tham số bộ chấm tự luận (4 âm tiết, 0.5·bigram + 0.5·trigram, ngưỡng 0.5) là giá trị ban đầu, hiệu chỉnh sau trên dữ liệu thật; bỏ n-gram thuộc `term` và thêm `term` vào câu chữ I4 ở §0 (D11(b)); sửa câu chữ cơ chế I3 ở §0 cho các ý của câu ESSAY (D11(c)); giữ tên `matched_count`/`total_points`; danh sách mã `CapabilityReason` và mặc định `HOLOCRON_LLM_MODEL_AUTHOR = HOLOCRON_LLM_MODEL_EXTRACT` (D7); các lớp K1–K7 của §10.1, kèm điều kiện có ≥ 3 ví dụ có đáp án trích từ tài liệu làm đối chứng độc lập (D6). §0 chỉ đổi đúng câu chữ D11(b)(c). Cùng đợt sửa theo review: luật so xung đột `theory_mcq4_v3` chỉ được duyệt hướng, chi tiết chờ duyệt câu 1b (§5.11, ADR-007); giới hạn `topic_keys` và route `/vault` trở về "đề xuất (chờ duyệt)" (§4, §5.8, §6.6, §17 D8); `source_doc_ids` là việc để sau (§5.9); luật loại nhãn `llm_data_recipient` chỉ so với biến khác rỗng (§5.12); điểm OCR chỉ định dạng hiển thị (§4, §5.7). **v0.7, bổ sung 2026-10-26 chiều** (vẫn v0.7; spec chưa có quy ước số phiên bản phụ kiểu v0.7.1): ghi các quyết định người dùng duyệt chiều 2026-10-26, mỗi chỗ sửa ghi "(duyệt 2026-10-26 chiều)": cột cơ chế I1 ở §0 (đóng D14; phần còn lại của §0 không đổi); hướng luật "đúng một đáp án" của bộ sinh câu lý thuyết: so trên dãy token chữ/số, theo khái niệm ở cả ba dạng, `GENERATOR_VERSION = "theory_mcq4_v3"` (§5.11, ADR-007; chi tiết chờ duyệt câu 1b); `TwinBatchRequest.format` chỉ ghi đè slot tính toán, `ESSAY` ⇒ 422 (§5.6, §6.3); `ExamBlueprint.source_exam_doc_id` cho phép null (§5.9); kiểm `Origin` cho request ghi và danh sách `Host` (§6.0, §13.1); kho in-memory ghi file PDF gốc vào thư mục tạm (§5.13); 503 khi app đang tắt (§6.0); tài liệu `failed` vẫn chuyển được sang `confirmed`, 409 cho tài liệu chưa có `chapter_code`, quy tắc `engine`/`conf` của dòng đã sửa và dòng gõ thêm, nhập lại khác `chapter_code` (§5.7, §6.5); `linked_chunk_ids` tính lại theo môn (§5.8, §12.1; giới hạn `topic_keys` ghi theo code là đề xuất, chờ duyệt, §17 D8); `RuntimeInfo` thêm chế độ lưu trữ và bên nhận dữ liệu LLM (§5.12, §11.2); route frontend theo code (§4; route `/vault` theo nhánh F2, chờ duyệt); ADR-006 mục 2 sửa theo `chapter_code` của form (§17 D9); sổ nợ ADR-004; phương án B của `docs/tauri-spike-plan.md`. Chi tiết chỉ được duyệt hướng ghi "đề xuất (chi tiết), chờ duyệt". **v0.7, bổ sung 2026-10-26** (vẫn v0.7): ghi các quyết định người dùng duyệt lúc 00:25 ngày 2026-10-26, mỗi chỗ sửa ghi "(duyệt 2026-10-26)": phần còn chờ của D8 gồm `TheorySlotSpec` kèm `format`, `theory_slots`, các field nullable của `BlueprintSlot` và `ReviewTicket.topic_key` (§5.9, §5.11); field form bắt buộc `chapter_code` của tài liệu nhập và nhãn ban đầu của chunk (§5.7, §5.8, §5.13, §6.5, §12.1); quy tắc sửa dòng và xác nhận trang (§5.7, §6.5); dạng câu lý thuyết do RNG tất định chọn (§5.11, ADR-007 mục 2); §0 không đổi. `chapter_code` theo form từng lệch ADR-006 mục 2 (LLM đề xuất theo heading); ADR-006 mục 2 đã sửa theo đợt duyệt 2026-10-26 chiều (§17 D9). **v0.7** (2026-10-25, tối): ghi các quyết định người dùng duyệt tối 2026-10-25, mỗi chỗ sửa ghi "(duyệt 2026-10-25)": câu chữ I1, I3, I4 ở §0 (bộ tính điểm chấm câu trả lời ngoài sandbox, "artifact lời giải theo `kind`" thay "Gantt", các ý của câu ESSAY, tái lập của bộ chấm); nhãn "đề xuất" thành "đã duyệt" cho `CapabilityError`, `LLM_UPSTREAM_ERROR`, quy ước `HOLOCRON_LLM_*`, token và header `X-Holocron-Token`, endpoint chấm tự luận và model `Subject` có `exam_format` (duyệt 2026-10-25, brief người dùng), hướng chấm bằng độ phủ n-gram âm tiết (bigram, trigram) báo "khớp x/y ý" (§5.12, §5.14, §6.0, §6.9, §11.2, §13.1); `HOLOCRON_DATA_DIR` không đặt thì dùng kho in-memory (§5.13, §11.2); `LOW_TEXT_SCORE` (§5.7); dual-pass không đặt `temperature` (§7.1); `page_source` cho item đến từ PDF (§5.4, §6.5, §7.4); mã lỗi của kho SQLite, gồm 503 chỉ khi kho bận (§6.0, §6.4); field của câu lý thuyết trong luồng twin (§5.9, §5.11). Cùng đợt ghi §16 và §17 D6–D14 (mỗi mục tách phần đã quyết và phần còn chờ duyệt); ADR-004…008 được sửa câu chữ, trạng thái không đổi. Các mục sau vẫn là "đề xuất (chờ duyệt)": danh sách mã `CapabilityReason`, `mock_extractor`, mặc định `HOLOCRON_LLM_MODEL_AUTHOR = HOLOCRON_LLM_MODEL_EXTRACT`, các tham số số học của bộ chấm (tách ý tối thiểu 4 âm tiết, trọng số 0.5/0.5, ngưỡng khớp ý 0.5; đề xuất, chưa hiệu chỉnh), tên field `matched_count` và câu chữ `text_score = null` khi trang failed do `PAGE_TIMEOUT` (§5.7, §5.12, §5.14, §11.2, §17 D7, D8); NHÁP các lớp kiểm chứng template ứng viên (§10.1, D6); dòng test cưỡng chế ở CLAUDE.md §9 cho ngoại lệ I1 và quy tắc ESSAY của I3 (§17 D11); câu hỏi mở D14 về câu chữ cơ chế I1 ở §0. **v0.6** (2026-10-25): ghi các quyết định người dùng chốt ngày 2026-10-24/25: mục tiêu sản phẩm (§1.2); `format` thêm `ESSAY` và `exam_format` theo môn (§1.5, §5.14, §6.8); chấm tự luận offline bằng n-gram, "MVP nếu còn thời gian" (§1.5, §5.14, §6.9); LLM là tuỳ chọn, chế độ khả năng thay quy tắc 503 (§5.12, §6.0, §11.4); adapter LLM qua gateway của ban tổ chức (§11.2, §11.5, §13.1); R10 chọn (c) (§8.1); kiểm Host luôn bật, token theo `HOLOCRON_REQUIRE_TOKEN` (§13.1); lịch lát cắt dọc (§16). 14 trong 15 điểm "đề xuất (chờ duyệt)" của v0.5 được duyệt 2026-10-25; điểm "503 khi thiếu key" bị thay (§17 D7). Phần mới vượt ra ngoài quyết định đã duyệt ghi "đề xuất (chờ duyệt)"; chỗ lệch ADR ghi "đã duyệt 2026-10-25, chờ sửa ADR-00x". **v0.5** (2026-10-24, Đợt 2b): thêm contract cho bộ nhập PDF, Knowledge Vault, blueprint và lưu trữ SQLite theo ADR-005…008 (§5.7–§5.13, §6.5–§6.7, §7.4); so khớp I3 sau chuẩn hoá NFC (§0, đã duyệt 2026-10-24); 7 điểm của ADR-008 mục 8–14 và R6 được duyệt 2026-10-24. Phần nào của các mục mới vượt ra ngoài ADR đã *Chấp nhận* thì ghi "đề xuất (chờ duyệt)". Các mục mới mô tả contract mục tiêu; code chưa triển khai trừ khi ghi "đã triển khai". **v0.4** (2026-10-23): chốt §1, câu chữ I2 và I4 ở §0 cho câu lý thuyết (ADR-007) và §13.1; cập nhật 2026-10-24: ba môn demo và §1.6, engine OCR theo ADR-008, R6 (§6.3, §10, §17). Thay đổi contract PHẢI qua PR sửa tài liệu này trước. |
| Phạm vi | Contract mục tiêu; phạm vi MVP-1 (26–29/10, chốt 2026-10-26) hẹp hơn, xem §1.5. MVP đa môn cho sinh viên tự ôn luyện: nhập PDF tài liệu và đề của môn bất kỳ (bộ nhập theo tầng, có màn hình xem lại) · Knowledge Vault · Ingestion Gate · câu tính toán do sandbox tính · câu lý thuyết có trích dẫn nguyên văn · desktop app Tauri. Ba môn demo: Kỹ thuật vi xử lý (ARM Assembly), Xác suất thống kê, Triết học Mác–Lênin (§1.5). CPU Scheduling (Round Robin, SJF) chỉ còn là fixture kiểm thử và walkthrough |
| Thiết kế đa môn | ADR-005 (pipeline câu hỏi không phụ thuộc topic), ADR-006 (Knowledge Vault đa môn), ADR-007 (câu lý thuyết và chấm câu trả lời), ADR-008 (bộ nhập PDF theo tầng). Cả bốn ADR ở trạng thái *Chấp nhận* (2026-10-23); engine OCR của ADR-008 chốt ngày 2026-10-24. Contract của các ADR này được viết vào §5.7–§5.13, §6.5–§6.7 và §7.4 ngày 2026-10-24 (Đợt 2b); code và `types/holocron.ts` cập nhật ở nhánh triển khai. Quyết định ngày 2026-10-25 từng lệch câu chữ của ADR-006, ADR-007 và ADR-008 ở ba chỗ: nhà cung cấp LLM (nhà cung cấp mặc định cũ thành nhà cung cấp do cấu hình quyết định, LLM tuỳ chọn), chấm tự luận offline trong MVP, và pdfplumber đã duyệt. Câu chữ ba ADR đã được sửa theo các quyết định này (duyệt 2026-10-25, §17 D9); trạng thái ADR không đổi. ADR-010 (bóc tách theo khối, cây mục, sổ bảo toàn; *Chấp nhận* 2026-11-01) thay ADR-006 mục 3 cho tài liệu có khối và bổ sung ADR-006 mục 5, 10; contract ở §5.7, §5.8, §5.13, §5.15, §6.5, §6.10, §12, §13.1. |
| Nguồn chân lý code | `holocron/backend/holocron_core/` (Pydantic v2) · `holocron/frontend/src/types/holocron.ts` (TypeScript, khớp 1:1) |
| Tài liệu liên quan | `docs/adr/`, `.claude/CLAUDE.md`, `holocron/backend/examples/*.json`, `docs/tauri-spike-plan.md` (kế hoạch spike đóng gói Tauri) |
| Quy ước | **PHẢI / KHÔNG ĐƯỢC / NÊN / CÓ THỂ** theo nghĩa RFC 2119 (MUST / MUST NOT / SHOULD / MAY) |

## Mục lục

0. [Tóm tắt & bất biến](#0-tóm-tắt--bất-biến)
1. [Bài toán, người dùng & phạm vi](#1-bài-toán-người-dùng--phạm-vi)
2. [Thuật ngữ](#2-thuật-ngữ)
3. [Kiến trúc](#3-kiến-trúc)
4. [User Flow Frontend ↔ Backend](#4-user-flow-frontend--backend)
5. [Data Contracts](#5-data-contracts)
6. [API Contracts](#6-api-contracts)
7. [Ingestion Gate](#7-ingestion-gate)
8. [Executable Template Contract](#8-executable-template-contract)
9. [Sandbox Runtime](#9-sandbox-runtime)
10. [Retry Budget & Fallback](#10-retry-budget--fallback)
11. [LLM Gate & cấu hình BYOK](#11-llm-gate--cấu-hình-byok)
12. [Knowledge Vault & trích dẫn 1-chạm](#12-knowledge-vault--trích-dẫn-1-chạm)
13. [Bảo mật & Threat Model](#13-bảo-mật--threat-model)
14. [Quan sát hệ thống](#14-quan-sát-hệ-thống)
15. [Kiểm thử & Định nghĩa "Done"](#15-kiểm-thử--định-nghĩa-done)
16. [Lộ trình](#16-lộ-trình)
17. [Rủi ro & quyết định](#17-rủi-ro--quyết-định)
18. [Phụ lục](#18-phụ-lục)

---

## 0. Tóm tắt & bất biến

Holocron sinh **câu hỏi song sinh (Twin Questions)** có kiểm chứng từ tài liệu học và đề thi cũ của môn học bất kỳ, để sinh viên tự ôn luyện. Câu tính toán được: mọi con số do code chạy trong sandbox cô lập tính ra (**Deterministic Core**). Câu lý thuyết: đáp án là trích dẫn nguyên văn từ **Knowledge Vault**. Dữ liệu đọc từ PDF hoặc ảnh luôn được con người xem lại và xác nhận trước khi dùng (**Ingestion Gate** và màn hình xem lại của bộ nhập PDF).

Năm bất biến sau PHẢI đúng ở mọi phiên bản; vi phạm bất kỳ bất biến nào là lỗi chặn release:

| ID | Bất biến | Cơ chế cưỡng chế |
|---|---|---|
| **I1** | Không con số nào do LLM tự viết. Đáp án đúng, mọi phương án nhiễu và artifact lời giải theo `kind` đến từ `solve()` / `make_distractors()` chạy trong Sandbox (duyệt 2026-10-25, ADR-005 bước 3). Bộ tính điểm chấm câu trả lời của người học (ví dụ `matched_count`/`total_points` của chấm tự luận offline) thuộc Deterministic Core và được tính bằng code ngoài sandbox; ngoại lệ này CHỈ áp dụng cho kết quả chấm câu trả lời của người học, KHÔNG áp dụng cho bất kỳ con số nào trong nội dung câu hỏi, đáp án hay lời giải (duyệt 2026-10-25). | LLM chỉ được sinh *mã template*; `TwinQuestion` câu tính toán chỉ được dựng từ `SandboxResult` (§9, §10); câu lý thuyết chỉ được dựng từ cặp khái niệm đã xác nhận qua bộ sinh tất định (`TheoryTrace`, ADR-007), không chứa con số do LLM viết (duyệt 2026-10-26 chiều). |
| **I2** | Mỗi phương án sai của MCQ4 ánh xạ đúng một `misconception_code` đã khai báo trước: trong template với câu tính toán, hoặc trong danh sách mã cố định `THEORY_MISCONCEPTIONS` của bộ sinh với câu lý thuyết (ADR-007). | Validator `TwinQuestion._format_rules`; `build_twin` ném `ValueError` khi gặp mã lạ → retry (§10); bộ sinh câu lý thuyết chỉ gán mã thuộc `THEORY_MISCONCEPTIONS`, có test cưỡng chế. |
| **I3** | Mọi câu hỏi có `Citation` với `verbatim_quote` là chuỗi con của `raw_text` chunk gốc. Phép so khớp đưa cả hai chuỗi về Unicode NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con; chỉ hai chuỗi tương đương chính tắc (canonical equivalence) mới được coi là khớp (đã duyệt 2026-10-24). | Validator `KnowledgeChunk._consistency`; `Citation` sinh từ `KnowledgeChunk.to_citation()`. Cùng quy tắc so khớp áp dụng cho `ConceptPair.definition_quote` (ADR-007, §5.8) và bằng chứng verdict của `AnswerJudge` (sau MVP). Các ý của câu ESSAY là chuỗi con của `definition_quote` theo cùng quy tắc so khớp: đưa cả hai chuỗi về NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con (duyệt 2026-10-25; câu chữ sửa, duyệt 2026-10-26, §17 D11(c)). |
| **I4** | Câu tính toán: `(template_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án, cùng artifact lời giải theo `kind` (duyệt 2026-10-25, ADR-005 bước 3). Câu lý thuyết: `(generator_version, pool_sha256, seed)` ⇒ cùng đề, cùng đáp án, cùng thứ tự phương án, trong đó `pool_sha256` là sha256 của các cặp khái niệm đã xác nhận cùng `subject_code` với slot (cách tính ở ADR-007 mục 2); dữ liệu của môn khác không làm đổi câu hỏi của môn này. Chấm tự luận offline: cùng `grader_version`, ngưỡng, `term`, các ý và câu trả lời (NFC) ⇒ cùng kết quả (duyệt 2026-10-25; thêm `term`, duyệt 2026-10-26, §17 D11(b)). | `random.Random(seed)` trong template; trộn phương án bằng `random.Random(f"{sha256}:{seed}")`; bộ sinh câu lý thuyết chỉ dùng `random.Random(f"{generator_version}:{pool_sha256}:{seed}")`; test cho thấy cùng nội dung nhập ở dạng NFC hay NFD cho cùng `pool_sha256`, và thêm cặp ở môn khác không làm đổi `pool_sha256` của môn này; walkthrough sinh `examples/*.json` (gồm ví dụ câu lý thuyết) giống từng byte. |
| **I5** | Không đề nào vào pipeline tính toán khi Gate còn issue `BLOCKING` chưa được con người xử lý. | `POST …/approve` trả 409; Frontend vô hiệu nút duyệt (`canApprove`); `POST /v1/blueprints` chỉ nhận item `approved=true`. |

## 1. Bài toán, người dùng & phạm vi

### 1.1 Bài toán
Sinh viên tự ôn thi thường gặp ba vấn đề:
- **Đáp số sai.** Hỏi chatbot bài tính toán (lập lịch CPU, xác suất, mạch điện…) thường nhận đáp số sai vì LLM tính nhẩm.
- **Lý thuyết không có nguồn.** Câu trả lời lý thuyết của chatbot không dẫn về slide hay giáo trình của chính môn mình học, nên không kiểm chứng được và đôi khi là bịa.
- **Tài liệu khó đọc bằng máy.** Tài liệu và đề cũ thường là PDF scan hoặc ảnh chụp mờ. OCR/VLM đọc sai một chữ số là sai cả lời giải mà không ai phát hiện, còn tiếng Việt dễ bị đọc sai dấu.

Bài toán xuất phát từ hoàn cảnh của chính người làm dự án và các bạn sinh viên cùng cảnh.

### 1.2 Người dùng mục tiêu
- **Duy nhất:** sinh viên tự học và ôn luyện các môn mình đang học, bằng tài liệu của chính môn đó (slide, giáo trình, đề thi cũ). Phạm vi không giới hạn ở một trường hay một ngành.
- Một máy, một người dùng. API key của LLM là **tuỳ chọn** (§11.4): không có key, app vẫn chạy offline các luồng chính; key, nếu có, do người dùng tự cấu hình (BYOK).
- **Người dùng đầu tiên** là chính người làm dự án, sinh viên năm 3, dùng app để ôn thi thật (đã duyệt 2026-10-25). Vì vậy MVP phải ổn định đủ cho việc ôn thật, không chỉ chạy được trong buổi demo.
- **Không phải người dùng:** giảng viên, nhà trường, trung tâm luyện thi. Sản phẩm không có chức năng ra đề cho lớp, quản lý lớp hay chấm bài cho người khác.

**Mục tiêu sản phẩm (đã duyệt 2026-10-25):**
- Desktop app Tauri chạy local cho sinh viên tự ôn (§1.4).
- Ở hackathon: nổi bật so với các sản phẩm khác, nhờ con số do sandbox tính (I1), trích dẫn nguyên văn (I3) và con người xác nhận dữ liệu đọc từ tài liệu (I5).
- Sau hackathon: phát triển thành app riêng. Thiết kế PHẢI mở rộng được theo hai trục, nhiều môn và nhiều dạng câu hỏi, mà không rẽ nhánh theo môn trong code (CLAUDE.md §1): môn mới đi qua dữ liệu (Vault, template, `subjects`), dạng câu mới đi qua trường phân biệt `format` và `question_kind` (§5.14).

### 1.3 Giải pháp
- **Một bộ nhập PDF theo tầng** (§1.5, ADR-008) dùng chung cho tài liệu học và đề thi; mọi kết quả tách đều qua **màn hình xem lại và sửa** trước khi lưu.
- **Knowledge Vault** lưu tài liệu học thành các chunk có trích dẫn nguyên văn (I3), gắn nhãn môn, chương, khái niệm (ADR-006).
- **Hai nhóm câu hỏi** (§1.5): câu tính toán do template chạy trong sandbox tính mọi con số (I1, I2, I4); câu lý thuyết lấy đáp án là trích dẫn nguyên văn từ Vault (I3). Không đủ căn cứ thì đưa vào `review_tickets`, không bịa.
- **Ba hình thức trả lời** (`format`, §5.14): trắc nghiệm `MCQ4`, câu trả lời số `NUMERICAL`, tự luận `ESSAY` (MVP nếu còn thời gian). Câu tự luận được chấm offline bằng code, chỉ báo khớp bao nhiêu ý, không cho điểm tuyệt đối.
- **Con người xác nhận dữ liệu trước khi tính** (I5): đề chỉ vào pipeline sau khi Gate hết issue BLOCKING.
- LLM là tuỳ chọn (§11.4). Khi có, LLM chỉ đọc, gắn nhãn, đề xuất và viết mã; mọi đầu ra của LLM đi qua một cổng kiểm (§3.3, ADR-001).

### 1.4 Nền tảng & tech stack
**Nền tảng:** desktop app chạy local, đóng gói bằng **Tauri**; backend Python chạy như tiến trình sidecar. Windows và Linux là nền tảng hạng nhất, mọi test PHẢI xanh trên cả hai. Webapp localhost hiện tại (Next.js dev server + uvicorn) chỉ là bước trung gian để phát triển. Backend chỉ nghe `127.0.0.1` (§13.1). Bản MVP-1 (chốt 2026-10-26) chưa dùng Tauri: đóng gói thành zip portable cho Windows (§1.5); Tauri chuyển sang 3–6/11 (§16).

**Tiêu chí chọn:** API LLM hiện có là key do ban tổ chức cấp, đi qua một gateway kiểu OpenAI, model `claude-haiku-5-5` (đã duyệt 2026-10-25; trước đó spec ghi một nhà cung cấp mặc định khác). Nhà cung cấp là cấu hình, đổi được; không nhà cung cấp nào là mặc định bắt buộc (§11.2). Các thành phần còn lại chọn theo tiêu chí tốt nhất hiện có cho đúng việc nó làm trong code.

| Thành phần | Lựa chọn | Lý do (gắn với cách code dùng) |
|---|---|---|
| Backend | Python ≥ 3.12, FastAPI | `Depends` + `Protocol` cho phép thay VLM/LLM/kho bằng stub trong test qua `app.dependency_overrides`, không gọi mạng (§3.2); OpenAPI tự sinh ở `/docs` |
| Model dữ liệu | Pydantic v2 | Validator cưỡng chế bất biến ngay ở biên hệ thống: I2 trong `TwinQuestion._format_rules`, I3 trong `KnowledgeChunk._consistency`; `model_json_schema()` là nguồn cho kiểu TypeScript (Phụ lục B) |
| Sandbox | Tiến trình con Python + ASTGuard + giới hạn OS | ADR-002: chạy được trên cả Windows (Job Object) và Linux (RLIMIT), không cần WASM |
| Frontend | Next.js 15 App Router, TypeScript strict | Rewrite `/api/holocron/*` về backend cùng origin nên không cần CORS (§6.0); `types/holocron.ts` khớp 1:1 với Pydantic |
| Desktop | Tauri | Đóng gói frontend và backend sidecar thành app cài trên máy; dùng WebView có sẵn của hệ điều hành nên bộ cài nhỏ hơn Electron |
| Lưu trữ | SQLite (kế hoạch; hiện in-memory) | Một file cục bộ, không cần server; optimistic locking nguyên tử bằng `UPDATE … WHERE version = :expected` (BE-003, §6.2). Dùng `sqlite3` của thư viện chuẩn thay SQLAlchemy, không thêm dependency (D5, đã duyệt 2026-10-25, §5.13) |
| LLM văn bản | **Tuỳ chọn** (§11.4). Endpoint bất kỳ theo giao thức OpenAI Chat Completions; hiện là gateway của ban tổ chức, model `claude-haiku-5-5` (§11.5) | Đề xuất nhãn môn/chương/dạng câu/mức nhận thức, đề xuất cặp khái niệm–định nghĩa, viết template. **Chỉ xử lý văn bản.** Địa chỉ và tên model là cấu hình `HOLOCRON_LLM_*` (§11.2). Có thể trỏ tới model chạy local qua `HOLOCRON_LLM_BASE_URL` (tuỳ chọn, MVP chưa làm) |
| VLM | Tuỳ chọn, BYOK, nhà cung cấp bất kỳ tương thích OpenAI nhận `image_url`. **Đang park** (2026-10-25) | Chưa kiểm gateway của ban tổ chức có nhận ảnh hay không (R4). VLM chỉ dùng cho trang có độ tin cậy thấp hoặc có hình vẽ. **Không có VLM, app vẫn chạy đủ luồng** nhờ tầng 1–3 của bộ nhập và màn hình xem lại; với PDF scan, điều này cần Tesseract và `vie.traineddata` do người dùng tự cài (G5, ADR-008 mục 8) |
| Bộ nhập PDF | Theo ADR-008: pypdfium2 (tầng 1); tầng 2 dùng Tesseract `vie` làm engine chính, gọi binary do người dùng tự cài qua subprocess, và RapidOCR + onnxruntime chỉ làm lượt đọc thứ hai để đối chiếu token số và mã (chốt 2026-10-24 sau phép đo CER trên scan giả lập); tầng 3 dùng `pdfplumber==0.11.10` (MIT), kéo theo `pdfminer.six==20260107` (MIT), đã duyệt 2026-10-25 (giấy phép kiểm trên PyPI cùng ngày; ADR-008 mục 1 đã sửa theo, duyệt 2026-10-25), chỉ thêm vào `pyproject.toml` khi làm tầng 3 | Thư viện đọc PDF và OCR chạy offline, giấy phép tương thích MIT (không GPL/AGPL; không dùng wheel `tesserocr` vì kèm thư viện LGPL/GPL), xử lý file không tin cậy trong tiến trình con có giới hạn. Ngoại lệ LGPL liên kết động của câu "không GPL/AGPL" này (duyệt 2026-10-27, Q4): chỉ bản zip portable Windows đủ (có RapidOCR, `build_portable.py --ocr full`) sẽ phân phối `geos*.dll` (GEOS 3.14.1, LGPL-2.1) do `shapely==2.2.0` (phụ thuộc bắc cầu của `rapidocr`) đóng kèm; bản lite không có GEOS. Điều kiện bắt buộc: kèm toàn văn LGPL-2.1 và mã nguồn GEOS đúng phiên bản (`licenses/geos-3.14.1.tar.bz2`, SHA-256 ghim); `THIRD_PARTY_NOTICES` của zip ghi DLL do delvewheel đổi tên và sửa bảng import (bản đã sửa đổi); build dừng nếu GEOS nằm ngoài `shapely.libs/` hoặc phiên bản shapely không có ánh xạ GEOS; `opencv_videoio_ffmpeg*.dll` (LGPL) bị bỏ khỏi zip. Điều kiện cưỡng chế ở `scripts/build_portable.py` (nhánh P4, chưa merge). Không đổi việc loại `tesserocr` |
| Công cụ chất lượng | ruff, mypy `--strict`, pytest; Biome, Vitest | Một bộ cổng chạy ở cả pre-commit và CI (ADR-004) |

### 1.5 Phạm vi MVP

**MVP-1 (chốt 2026-10-26).** Spec v0.7 là contract mục tiêu, không phải phạm vi MVP. MVP-1 là mục tiêu duy nhất tới hết 29/10/2026. Hạn nộp hackathon là Chủ nhật 8/11/2026; lịch sau MVP-1 ở §16.
- **Sản phẩm:** bản Windows dạng zip portable, chạy offline, không cần API key.
- **Luồng:** tạo môn → nhập PDF (lớp chữ + OCR) → xem lại trang → xác nhận vào Vault → xác nhận cặp khái niệm → sinh câu MCQ4 lý thuyết tất định → làm bài → xem đáp án kèm trích dẫn nguyên văn và số trang → lịch sử làm bài và ôn lại câu sai.
- **Lịch sử làm bài:** contract là đề xuất riêng, chờ duyệt. Đúng/sai và mọi con số do backend tính (I1).
- **Đóng gói:** Python embeddable (giấy phép PSF); backend phục vụ frontend tĩnh và tự mở trình duyệt. Người dùng tự cài Tesseract theo hướng dẫn. Không dùng PyInstaller. Tauri chuyển sang 3–6/11. Launcher đặt `HOLOCRON_DATA_DIR` để lưu SQLite (lịch sử làm bài cần lưu bền; yêu cầu người dùng 2026-10-26: "bảng lượt làm bài trong SQLite") (§5.13, §11.2).
- **Blueprint:** MVP-1 chỉ làm luồng slot lý thuyết (`theory_slots`), cùng các endpoint GET blueprint (§6.7). Giữ `POST /v1/blueprints` làm đường tạo blueprint cho slot lý thuyết (duyệt 2026-10-28, câu 2). `BlueprintCreate` vẫn có `item_ids` (§5.9) nhưng `item_ids` khác rỗng ⇒ 422 "Blueprint có câu tính toán chưa được hỗ trợ trong bản này", trước mọi lần ghi (duyệt 2026-10-28, câu 15; chỉ áp cho đường blueprint này, luồng sinh câu song sinh có tính toán `POST /v1/blueprints/{id}/twins` giữ nguyên). Kiểm I5 cho `item_ids` (mỗi item PHẢI `approved=true` và không `non_cloneable`, ngược lại 409, §6.7 bước 2 của contract mục tiêu) là mục tiêu sau 29/10, vì MVP-1 chưa nhận item nào ở đường này.
- **Sau MVP-1:** câu tính toán, tầng 3 (bảng) (suy ra từ phạm vi MVP-1 chỉ gồm lớp chữ + OCR), tự luận (essay), `capabilities`, kiểm `Origin`, `RuntimeInfo` mở rộng.
- Xếp mục tiêu G1–G6 vào sau MVP-1: suy ra, chờ người dùng xác nhận. G5, G6 (chạy không VLM, không LLM) là yêu cầu của chính MVP-1 (offline, không cần key). Tiêu chí xong của MVP-1 là luồng nêu ở mục MVP-1.

**Đa môn.** App nhận PDF của môn bất kỳ. Code KHÔNG ĐƯỢC rẽ nhánh theo môn hay topic (CLAUDE.md §1); tri thức riêng của dạng bài nằm trong template và dữ liệu. CPU Scheduling (RR/SJF) chỉ còn là fixture kiểm thử và walkthrough `examples/`, không phải môn demo hay phạm vi.

**Hai nhóm câu hỏi:**

| Nhóm | Đáp án đến từ | Bất biến chính | Khi không đủ căn cứ |
|---|---|---|---|
| Câu tính toán được | `solve()`/`make_distractors()` chạy trong sandbox; template đã kiểm chứng, hoặc template ứng viên do LLM viết rồi được kiểm chứng và người duyệt (§8, §10) | I1, I2, I4, I5 | `ReviewTicket` |
| Câu lý thuyết | Trích dẫn nguyên văn từ chunk trong Vault. Câu MCQ4 sinh tất định từ các cặp khái niệm–trích dẫn đã được người dùng xác nhận (cặp do LLM đề xuất nếu có LLM, hoặc người dùng tự bôi trong chunk), gồm ba dạng: ghép khái niệm, điền khuyết, chọn định nghĩa đúng với phương án nhiễu cùng chương (ADR-007). Câu tự luận "Trình bày khái niệm X" (MVP nếu còn thời gian, §5.14). LLM không soạn câu | I2, I3, I4 | `ReviewTicket`, không bịa |

Nhóm câu (`question_kind`, ADR-005 mục 1) nói **câu hỏi lấy từ đâu** và giữ nguyên hai giá trị `computational`, `theory`. **Hình thức trả lời** là trường riêng `format` (đã duyệt 2026-10-25, §5.14):

| `format` | Người học trả lời | Dùng với | Ai chấm |
|---|---|---|---|
| `MCQ4` | Chọn một trong 4 phương án | `computational`, `theory` | Code so khoá đã chọn với `correct_key` |
| `NUMERICAL` | Nhập một số | `computational` | Code so với `numerical_answer` trong dung sai |
| `ESSAY` | Viết đoạn văn | `theory`, chỉ dạng "Trình bày khái niệm X" trong MVP | Code chấm offline bằng độ phủ n-gram, báo "khớp x/y ý" (§5.14); `AnswerJudge` bằng LLM là nâng cấp tuỳ chọn sau MVP |

**Hình thức thi theo môn (đã duyệt 2026-10-25).** Mỗi môn có `exam_format ∈ {mcq, essay, mixed}`, vì năm 1–2 đa số môn thi trắc nghiệm, còn năm 3 đa số thi tự luận. `exam_format` chỉ là mặc định cho hình thức của slot lý thuyết và để UI hiển thị (§5.14, §6.8). **Không có quy tắc hạ tự luận xuống trắc nghiệm:** code KHÔNG ĐƯỢC tự đổi slot `ESSAY` thành `MCQ4`; khi chấm tự luận chưa khả dụng, người dùng tự chọn hình thức của từng slot.

**Tự luận trong MVP (đã duyệt 2026-10-25, ADR-007 mục 5a; "MVP nếu còn thời gian").** Lịch chốt 2026-10-26: tự luận không thuộc MVP-1, làm trong 30/10–2/11 (§16); thay mốc "trước hết ngày 29/10" của bản 2026-10-25. Bản đang chạy chưa có phần tự luận thì capability `essay_grading_offline` báo `not_implemented` (§11.4).
- **Phạm vi:** chỉ dạng "Trình bày khái niệm X". Đáp án tham chiếu là `definition_quote` của một `ConceptPair` đã xác nhận (ADR-007), nên giữ I3 với so khớp NFC.
- **Chấm offline**, không cần LLM, thuộc Deterministic Core ("bộ tính điểm", CLAUDE.md §2.1): độ phủ n-gram âm tiết (bigram, trigram) sau chuẩn hoá NFC, theo từng ý tách từ `definition_quote`.
- **Hiển thị:** "khớp x/y ý" và danh sách ý còn thiếu. KHÔNG có điểm số tuyệt đối.
- **Giới hạn phải ghi trên UI:** người học diễn đạt khác với tài liệu (đồng nghĩa, đổi trật tự, viết tắt) có thể bị chấm sót ý. Kết quả là gợi ý để tự đối chiếu với trích dẫn, không phải điểm thi.
- **Khi có LLM:** `AnswerJudge` (ADR-007 mục 5) là nâng cấp tuỳ chọn sau MVP; điểm và kết luận vẫn do code tính.

**Bộ nhập PDF theo tầng** (ADR-008), dùng chung cho tài liệu học (đi vào Vault) và đề thi (đi vào Ingestion Gate, blueprint, twin):
1. Tầng 1: đọc lớp chữ có sẵn của PDF số.
2. Tầng 2: OCR cho trang không có chữ (PDF scan), hỗ trợ tốt tiếng Việt có dấu.
3. Tầng 3: tách bố cục cho bảng và công thức.
4. Tầng 4: VLM tuỳ chọn, người dùng tự nhập key, chỉ dùng cho trang có độ tin cậy thấp hoặc có hình vẽ. Đang park (2026-10-25) vì chưa kiểm gateway hiện có nhận ảnh hay không (R4).

Phân loại (môn, chương, dạng câu, mức nhận thức) do LLM đề xuất trên văn bản đã tách khi có cấu hình LLM, không dùng VLM; không có LLM thì người dùng nhập hoặc giữ giá trị mặc định (§5.4, §11.4). Luôn có màn hình xem lại và sửa trước khi lưu, vì OCR và VLM có thể đọc sai số liệu. Không tự train model.

**Môn demo.** Bộ tài liệu demo PHẢI có ít nhất **một PDF scan** và **một PDF có bảng** lấy từ tài liệu thật, để luồng OCR (tầng 2) và tầng 3 được thử trên dữ liệu thật; người làm dự án cung cấp các file này. Phần còn lại có thể do nhóm tự soạn. Tài liệu thật có thể có bản quyền nên chỉ giữ cục bộ trong `eval_data/` (đã nằm trong `.gitignore`), không commit (CLAUDE.md §6); repo chỉ chứa file mẫu tự tạo.

| Môn | Vai trò trong demo |
|---|---|
| Kỹ thuật vi xử lý (viễn thông, ARM Assembly; đề cương khoa Kỹ thuật Điện, Điện tử) | Câu tính toán: giá trị thanh ghi và cờ N, Z, C, V của CPSR sau một đoạn lệnh. Cần template mới, có test tái lập I4 và property test ≥ 100 seed (§8.4, CLAUDE.md §9) trước khi vào kho template đã kiểm chứng. Lời giải cần artifact dạng bảng (thanh ghi, cờ CPSR, ô nhớ) theo ADR-005 |
| Xác suất thống kê | Môn có công thức: câu tính toán bằng template do LLM viết (bậc ứng viên rồi người duyệt), cùng câu lý thuyết về định nghĩa và công thức |
| Triết học Mác–Lênin | Môn lý thuyết thuần: chỉ có câu lý thuyết từ Vault |

Quyết định ngày 2026-10-24: Kỹ thuật vi xử lý thay Hệ điều hành làm môn demo. Template Vi xử lý chưa có, nên đây là rủi ro mới cho G1 (R10, §17). CPU Scheduling (RR, SJF) chỉ còn là fixture kiểm thử và walkthrough `examples/`; hai template đã kiểm chứng của nó vẫn giữ nguyên (§8.4). Hệ điều hành là môn mở rộng sau MVP (§1.6).

**Mục tiêu MVP** (xếp sau MVP-1: suy ra, chờ người dùng xác nhận; G5, G6 (chạy không VLM, không LLM) là yêu cầu của chính MVP-1 (offline, không cần key); xem mục MVP-1 ở trên):
- **G1.** Với mỗi môn demo, luồng chạy end-to-end trên máy người dùng: nhập PDF → xem lại và sửa → Vault và đề đã duyệt → blueprint → twin có trích dẫn.
- **G2.** Tính đúng: mọi con số hiển thị đến từ sandbox (I1); mọi câu lý thuyết có trích dẫn nguyên văn (I3); không đủ căn cứ thì vào `review_tickets`.
- **G3.** Hiệu năng: sinh một twin tính toán p95 ≤ 3 s, không tính thời gian LLM viết template; Gate phản hồi sau mỗi lần sửa ô p95 ≤ 500 ms. Ngưỡng thời gian cho bộ nhập PDF chốt sau khi đo (ADR-008).
- **G4.** Tái lập: chạy lại cùng `(template_sha256, seed)` cho kết quả giống từng byte (trừ `twin_id`, `generated_at`, `sandbox_duration_ms`).
- **G5.** Không có VLM, app vẫn chạy đủ luồng G1. Với PDF scan, điều này cần Tesseract và `vie.traineddata` do người dùng tự cài (ADR-008 mục 8).
- **G6.** Không có LLM, app vẫn chạy offline lát cắt dọc ở §16 (PDF → Vault → câu lý thuyết MCQ → màn luyện tập); nhãn và cặp khái niệm do người dùng nhập. Tính năng nào cần LLM thì báo không khả dụng qua `RuntimeInfo.capabilities` (§5.12, §11.4), không làm hỏng phần còn lại (đã duyệt 2026-10-25).

**Phi mục tiêu (MVP):**
- **`AnswerJudge` chấm tự luận bằng LLM: sau MVP.** Cần bộ câu trả lời mẫu có nhãn để đo độ đúng, chưa làm kịp trong thời hạn hackathon. CLAUDE.md §2.1 giữ chỗ cho `AnswerJudge` trong kiến trúc; thiết kế ở ADR-007 mục 5.
- **Chấm tự luận offline bằng n-gram: MVP nếu còn thời gian** (đã duyệt 2026-10-25, ADR-007 mục 5a). Chỉ dạng "Trình bày khái niệm X", chỉ báo "khớp x/y ý" (§5.14). Không thuộc MVP-1; lịch 30/10–2/11 (chốt 2026-10-26, §16).
- Tự train hoặc fine-tune model.
- Bắt buộc có VLM; nhận dạng chữ viết tay (lịch 3–6/11 chỉ có thử chữ viết tay ở mức thử nghiệm, chốt 2026-10-26, §16).
- Trích dẫn bằng bounding-box pixel (dùng `page_number` + `verbatim_quote`, ADR-003).
- Đa người dùng, đăng nhập, đồng bộ đám mây, bản web công khai, ứng dụng mobile.
- Người dùng sửa template (D1). Câu phụ thuộc hình vẽ vẫn gắn `non_cloneable`.

**Ràng buộc:**
- Local-first: không phụ thuộc dịch vụ trả phí nào. API LLM/VLM là tuỳ chọn, do người dùng tự cấu hình; khi dùng, dữ liệu rời máy theo §13.1.
- Lưu trữ: in-memory hiện tại → SQLite bằng `sqlite3` của thư viện chuẩn (thiết kế ở §5.13; D5 đã duyệt 2026-10-25).

### 1.6 Tầm nhìn sau MVP
Ghi chú theo yêu cầu người dùng (2026-10-24): sau MVP, dự án sẽ cố gắng làm tiếp các phần đã bị cắt khỏi MVP, liệt kê dưới đây. Lịch chốt 2026-10-26: MVP-1 xong hết 29/10; phần sau MVP-1 làm từ 30/10 tới hạn nộp hackathon Chủ nhật 8/11/2026 theo §16; phần không kịp trước hạn nộp làm sau hackathon.

- `AnswerJudge` chấm câu trả lời tự luận theo rubric: LLM trả verdict từng ý kèm bằng chứng, code tính điểm (CLAUDE.md §2.1, ADR-007). Đây là nâng cấp tuỳ chọn của bộ chấm offline (§5.14), chỉ chạy khi có cấu hình LLM.
- Chấm tự luận offline bằng n-gram (§5.14), nếu không làm kịp trong MVP; các dạng tự luận khác "Trình bày khái niệm X" (so sánh, giải thích, nêu ví dụ), mỗi dạng cần rubric neo vào trích dẫn nguyên văn.
- Đáp án nhiều giá trị dạng bản ghi cho câu `NUMERICAL` (R10 phương án (a), §8.1), qua một ADR riêng.
- Trỏ `HOLOCRON_LLM_BASE_URL` tới model chạy local trên máy người dùng (tuỳ chọn; giao thức OpenAI nên không cần đổi contract, §11.2).
- Tìm kiếm ngữ nghĩa trong Vault bằng embedding chạy offline, bổ sung cho tìm kiếm từ khoá.
- Thêm dạng bài tính toán qua kho template đã kiểm chứng.
- Sandbox lớp L4 (nsjail/seccomp trên Linux, AppContainer trên Windows) và bản macOS.
- **Frontend (duyệt 2026-10-26 chiều, §4):** render PDF bằng pdfjs (ảnh trang ở màn xem lại của bộ nhập, mở trích dẫn đúng trang và tô `verbatim_quote`, §5.7, §12.2). MVP xem lại theo văn bản từng dòng và hiện trích dẫn dạng quote kèm số trang. Lịch sử lượt làm bài từng được xếp ở đây (duyệt 2026-10-26 chiều); quyết định chốt 2026-10-26 đưa lịch sử làm bài và ôn lại câu sai vào MVP-1 (§1.5).
- **Giải thích đáp án bằng LLM (chốt 2026-10-26, lịch 30/10–2/11, §16):** TUỲ CHỌN, chỉ chạy khi người dùng tự thêm key; mỗi giải thích neo vào trích dẫn nguyên văn (I3); kết quả được lưu lại, không gọi LLM lại cho cùng câu. Thiết kế chờ duyệt.
- **Đọc lại trang `failed` (duyệt 2026-10-26 chiều, §5.7, §6.5).** MVP không có thao tác đọc lại một trang; người dùng xoá tài liệu rồi nhập lại.
- **DOCX.** Bộ nhập tài liệu của MVP chỉ nhận PDF. Tài liệu demo Vi xử lý có bản `.docx`, nhưng bản PDF là đủ cho MVP.
- **Hệ điều hành** là môn mở rộng khi có tài liệu, tận dụng hai template RR/SJF đã kiểm chứng. Không xoá template hay `examples/`.
- **Tầng OCR**, các phần bị lùi theo quyết định engine ngày 2026-10-24 (ADR-008 mục 8–10):
  - đóng gói Tesseract cùng app, kèm kiểm giấy phép của bản build (MVP yêu cầu người dùng tự cài);
  - Docling như một worker tuỳ chọn, tải về riêng (ADR-008, mục "Đánh đổi Docling");
  - nhận dạng công thức trên trang scan khi không có VLM;
  - xác minh trên dữ liệu thật các ngưỡng còn "chưa xác minh": chữ viết tay, CER văn bản giữa hai lượt đọc.
- **Tầm nhìn dài hạn:**
  - Hỗ trợ mọi môn.
  - Bộ nhập đọc được hầu hết định dạng (PDF, DOCX, PPTX, ảnh), đi qua cùng `Protocol` của ADR-008. Mỗi định dạng mới cần ADR và dependency được duyệt.
  - LLM phân tích tài liệu, học dạng bài bằng cách viết template rồi qua kiểm chứng (§8, §10), và đề xuất câu hỏi qua cổng kiểm. Mỗi ý neo vào trích dẫn nguyên văn (I3); mọi con số do sandbox tính (I1). LLM soạn câu lý thuyết cần một ADR mới (MVP sinh câu lý thuyết tất định, ADR-007).
  - Không tự train hay fine-tune model.

## 2. Thuật ngữ

| Thuật ngữ | Định nghĩa |
|---|---|
| Ingestion Item | Một câu hỏi đọc từ trang đề, gồm stem, 4 phương án, bảng dữ liệu (2 lượt đọc) và `GateReport` |
| Dual-pass | Hai lượt trích xuất độc lập trên cùng trang (khác prompt; không đặt nhiệt độ, duyệt 2026-10-25, §7.1); lệch nhau ở ô nào thì ô đó nghi đọc sai |
| Gate Issue | Phát hiện của Ingestion Gate; gắn toạ độ `(row, col)` khi thuộc một ô |
| CPUSchedulingProblem | Đề CPU Scheduling đã chuẩn hoá từ bảng qua Gate — đầu vào duy nhất cho `solve()` hiện nay. Bị xoá theo ADR-005 bước 2, thay bằng `params` đã qua `input_schema` (§5.10) |
| Exam Blueprint | Ma trận đề: danh sách slot (dạng câu, topic, archetype phương án nhiễu) dựng từ các item đã duyệt và slot lý thuyết theo chương (§5.9) |
| Page Extraction | Kết quả đọc một trang PDF của bộ nhập theo tầng: các dòng, điểm tin cậy, cờ trang, đối chiếu chéo và lý do cần xem kỹ (§5.7, ADR-008) |
| Cross-check | Đối chiếu token số và mã giữa lượt đọc chính (Tesseract) và lượt đọc thứ hai (RapidOCR) của cùng trang (ADR-008 mục 11) |
| Concept Pair | Cặp khái niệm – trích dẫn định nghĩa nguyên văn trong một chunk; nguyên liệu của câu lý thuyết khi đã được người dùng xác nhận (§5.8, ADR-007) |
| `question_kind` | Nhóm câu hỏi, tức câu hỏi lấy từ đâu: `computational` (sandbox tính) hoặc `theory` (sinh tất định từ Vault) (ADR-005 mục 1) |
| `format` | Hình thức trả lời: `MCQ4`, `NUMERICAL` hoặc `ESSAY` (§1.5, §5.14). Độc lập với `question_kind` |
| `exam_format` | Hình thức thi của một môn: `mcq`, `essay` hoặc `mixed`; chỉ là mặc định cho slot lý thuyết và để UI hiển thị (§5.14, §6.8) |
| Ý (essay point) | Một đoạn cắt nguyên văn từ `definition_quote` theo dấu câu, đơn vị để chấm tự luận offline (§5.14) |
| Capability | Một khả năng của app (OCR, sinh câu lý thuyết, đề xuất cặp khái niệm bằng LLM…) kèm `available` và lý do; `GET /v1/runtime` trả danh sách này (§5.12, §11.4) |
| Executable Template | Script Python tuân thủ hợp đồng §8, do LLM sinh hoặc pre-computed đã kiểm chứng |
| Twin Question | Câu hỏi đẳng cấu sinh từ template + seed |
| Misconception | Lỗi nhận thức có mã (`RR_TREATED_AS_FCFS`…); mỗi phương án nhiễu hiện thành *badge* trên UI |
| Gantt Segment | Một đoạn CPU chạy một tiến trình trong `[start, end)`; do sandbox sinh. Theo ADR-005 bước 3, sơ đồ Gantt là một `TimelineArtifact` (§5.11); I1 và I4 nói "artifact lời giải theo `kind`" thay cho "Gantt" (duyệt 2026-10-25) |
| BYOK | Bring Your Own Key: người dùng tự cấu hình API key cho LLM tương thích OpenAI. Key là tuỳ chọn (§11.4); key hiện có do ban tổ chức cấp, đi qua gateway của ban tổ chức (§11.5) |

## 3. Kiến trúc

### 3.1 Sơ đồ thành phần

```mermaid
flowchart LR
  subgraph FE["Frontend — Next.js 15 (holocron/frontend)"]
    UP[UploadForm] --> TA[TableAuditor]
    TA --> TW[Twin Viewer + Gantt]
    TW --> PDF[PDF.js Split-View]
  end
  subgraph BE["Backend — FastAPI (holocron/backend/holocron_core)"]
    R[routers/api.py: app<br/>routers/runtime.py · ingestion.py · blueprints.py<br/>routers/deps.py: Depends]
    subgraph LLMGate["LLM Gate — KHÔNG tin cậy"]
      EX[services/extraction.py<br/>DocumentExtractor]
      AU[TemplateAuthor<br/>LLM BYOK, tuỳ chọn]
    end
    subgraph Core["Deterministic Core — tin cậy"]
      G[services/ingestion_gate.py<br/>5 tín hiệu]
      P[services/pipeline.py<br/>retry & fallback]
      AST[sandbox/sandbox.py<br/>ASTGuard]
      SB[sandbox/_sandbox_child.py<br/>tiến trình con + giới hạn OS]
      TL[infrastructure/template_library.py<br/>template đã kiểm chứng]
      EG[Bộ chấm tự luận offline<br/>n-gram, §5.14]
    end
    D[(domain/schemas.py)]
  end
  FE -- "/api/holocron/* (Next rewrite)" --> R
  R --> EX --> G
  R --> G
  G -- self-solve --> SB
  R --> P
  P --> AU
  P --> AST --> SB
  P --> TL
  R --> EG
  G -.-> D
  P -.-> D
```

### 3.2 Phân tầng & quy tắc phụ thuộc

| Tầng | Package | Được import | KHÔNG ĐƯỢC import |
|---|---|---|---|
| Domain | `domain/` | thư viện chuẩn, `pydantic` | mọi tầng khác |
| Sandbox | `sandbox/` | `domain/` (gián tiếp qua kiểu), thư viện chuẩn | `services/`, `routers/`, mạng |
| Services | `services/` | `domain/`, `sandbox/`, `infrastructure/` | `routers/`, `fastapi` |
| Infrastructure | `infrastructure/` | `domain/` | `services/`, `routers/` |
| Routers | `routers/` | tất cả tầng dưới | — |

Phụ thuộc bên ngoài (VLM, LLM, storage) PHẢI đi qua `Protocol` (`DocumentExtractor`, `TemplateAuthor`, `TemplateRegistry`, `CitationProvider`; sẽ thêm `PdfReader` theo ADR-008 mục 2, các repository lưu trữ ở §5.13 và `ChatCompletionClient` cho adapter LLM ở §11.5) và được cấp qua `Depends` hoặc tham số, để test thay được mà không gọi mạng.

### 3.3 Ranh giới tin cậy
- **Không tin cậy:** file người dùng tải lên; mọi output VLM (bảng, stem, phương án); mọi output LLM, kể cả mã template; câu trả lời tự luận người học gõ vào (chỉ là văn bản để so n-gram, §5.14, không bao giờ được thực thi hay gửi tới LLM khi chưa có `AnswerJudge`).
- **Tin cậy:** `domain/schemas.py`, ASTGuard, sandbox runner, template `precomputed_verified`, dữ liệu ô có `verified_by_human=true`.
- Dữ liệu không tin cậy PHẢI đi qua ít nhất một cổng tin cậy trước khi ảnh hưởng tới con số hiển thị: bảng → Ingestion Gate; mã template → ASTGuard + sandbox + validator `TwinQuestion`.

## 4. User Flow Frontend ↔ Backend

```mermaid
sequenceDiagram
  actor U as Sinh viên
  participant FE as Frontend (Next.js)
  participant API as FastAPI
  participant EX as DocumentExtractor
  participant G as Ingestion Gate
  participant SB as Sandbox
  U->>FE: Chọn ảnh/PDF đề + dạng bài (+ quantum)
  FE->>API: POST /v1/ingestion/upload (multipart)
  API->>API: sniff magic bytes, sha256 → document_id
  API->>EX: extract() — lượt A & lượt B
  EX-->>API: DualPassExtraction
  API->>G: table → CPUSchedulingProblem
  G->>SB: self-solve (params_override)
  SB-->>G: SandboxResult
  G-->>API: GateReport (5 tín hiệu)
  API-->>FE: 201 UploadResponse
  FE->>U: Bảng; ô BLOCKING viền đỏ nhấp nháy
  loop đến khi không còn BLOCKING
    U->>FE: Click ô, sửa hoặc chọn giá trị từ lượt đọc kia
    FE->>API: PATCH /v1/ingestion/items/{id}/table (expected_version)
    API->>G: apply_patches + chạy lại Gate & self-solve
    API-->>FE: 200 IngestionItem (version+1)
  end
  U->>FE: Approve & Run Simulation
  FE->>API: POST /v1/ingestion/items/{id}/approve
  API-->>FE: 200 (approved=true)
  FE->>API: POST /v1/blueprints {theory_slots}  (bản này: item_ids khác rỗng ⇒ 422; mục tiêu sau 29/10: {item_ids})
  API-->>FE: 201 ExamBlueprint
  FE->>API: POST /v1/blueprints/{id}/twins {count: 3}
  API->>SB: template × seed (retry & fallback)
  API-->>FE: 201 TwinBatch (TwinQuestion[] kèm gantt)
  FE->>U: Gantt + 3 Twin + badge misconception + trích dẫn 1-chạm
```

| Bước | Màn hình / hành động | Endpoint | Trạng thái UI khi thành công | Xử lý lỗi trên UI |
|---|---|---|---|---|
| 1 | `/ingestion` (không có query) — chọn file, dạng bài, quantum tuỳ chọn | `POST /v1/ingestion/upload` | Chuyển tới `/ingestion?item={item_id}` | 400/413/415/422 hiện ngay dưới form, giữ nguyên lựa chọn |
| 2 | `/ingestion?item={item_id}` — tải item | `GET /v1/ingestion/items/{id}` | Hiện stem, phương án, kết quả tự giải, `TableAuditor` | 404 → thông báo + link về trang tải lên |
| 3 | Sửa ô (Enter lưu, Esc huỷ, hoặc chọn giá trị từ `alternatives`) | `PATCH …/table` | Bảng cập nhật, issue tính lại, `version` tăng | 409 → tải lại item mới nhất và báo người dùng sửa lại; 422 → hiện lỗi tại ô |
| 4 | Nút **Approve & Run Simulation** (chỉ bật khi không còn BLOCKING) | `POST …/approve` | Nhãn "Đã duyệt", khoá sửa bảng | 400 (non-cloneable) / 409 (còn BLOCKING) → hiện chi tiết |
| 5 | Tạo blueprint. Bản này (duyệt 2026-10-28): chỉ slot lý thuyết (`theory_slots`); `item_ids` khác rỗng ⇒ 422 "Blueprint có câu tính toán chưa được hỗ trợ trong bản này". Mục tiêu sau 29/10 (nợ ADR-004): tạo từ item đã duyệt | `POST /v1/blueprints` | Lưu `blueprint_id` | 422 (body sai, `item_ids` khác rỗng, `source_chunk_id` sai); 409 `CapabilityError` (slot `ESSAY`). Mục tiêu sau 29/10: 409 nếu có item chưa duyệt |
| 6 | `/twins?blueprint={blueprint_id}` — sinh 3 Twin | `POST /v1/blueprints/{id}/twins` | Câu hỏi + artifact lời giải + badge | `review_tickets` không rỗng → hiện slot cần xem lại thủ công |
| 7 | Click trích dẫn | — | MVP: hiện `verbatim_quote` dạng trích dẫn kèm `doc_id` và `page_number`. Mở slide đúng trang và highlight bằng pdfjs là sau MVP (duyệt 2026-10-26 chiều, §1.6, §12.2) | — |

Bước 1–4 đã triển khai ở Tuần 2. Bước 5–7 là contract chốt, triển khai ở Tuần 2 Ngày 3–4 (§16). Bước 5 dùng contract `BlueprintCreate` mới ở §5.9 và §6.7.

**Route của frontend (theo code trên develop; danh sách route chưa có quyết định riêng).** ID của bản ghi đi qua query string, không dùng route động, để build được ở chế độ static export (`docs/tauri-spike-plan.md`, phương án B). Các route `/ingestion/{item_id}` và `/blueprints/{blueprint_id}` của bản trước không còn.

| Route | Màn hình | Nguồn trong code |
|---|---|---|
| `/` | Màn chạy lần đầu: khả năng của máy này (`GET /v1/runtime`, §5.12) và bước tiếp theo | `src/app/page.tsx`, `components/start/StartScreen.tsx` |
| `/ingestion`, `/ingestion?item=…` | Không có `item`: form tải đề (§6.1). Có `item`: kiểm duyệt một ingestion item (bước 2–4) | `src/app/ingestion/page.tsx`, `components/ingestion/` |
| `/twins`, `/twins?blueprint=…` | Không có `blueprint`: ô nhập mã bộ đề. Có `blueprint`: sinh và xem twin (bước 6) | `src/app/twins/page.tsx`, `components/twins/TwinsView.tsx` |
| `/practice` | Màn luyện tập của lát cắt dọc: chọn môn, tạo đề từ cặp khái niệm đã xác nhận, làm từng câu | `src/app/practice/page.tsx`, `components/practice/` |
| `/vault?subject=&doc=&page=&tab=` | Knowledge Vault: `subject` là môn đang xem (mọi truy vấn Vault lọc theo môn, §6.6); `doc` (tài liệu), `page` (trang) và `tab` (tab đang mở) giữ vị trí xem để mở lại đúng chỗ | `src/app/vault/page.tsx`, `src/lib/vault.ts` (`tab` là `documents` hoặc `chunks`, mặc định `documents`). Có trong code từ PR #48 (đã merge vào develop); người dùng chưa duyệt riêng danh sách route (đề xuất, §17 D8) |

Sau MVP (§1.6): render PDF bằng pdfjs (ảnh trang ở màn xem lại, mở trích dẫn đúng trang). Lịch sử làm bài và ôn lại câu sai thuộc MVP-1 (chốt 2026-10-26, §1.5); route và contract của chúng là đề xuất riêng, chờ duyệt.

**Luồng nhập tài liệu bằng bộ nhập PDF (contract §6.5, chưa triển khai):** chọn PDF, `subject_code` và `chapter_code` (một file = một chương, duyệt 2026-10-26) → `POST /v1/documents` (202) → hỏi trạng thái bằng `GET /v1/documents/{id}` cho tới khi hết trang `pending` → xem lại từng trang (`GET …/pages/{page_number}`), sửa dòng (`PATCH …/lines`) → xác nhận trang hoặc cả tài liệu. Ở màn xem lại, điểm OCR do backend trả chỉ được định dạng để hiển thị, không tính lại (duyệt 2026-10-26 chiều, §5.7). Tài liệu học đã xác nhận được cắt chunk vào Vault (§6.6); trang đề đã xác nhận được tạo thành `IngestionItem` và đi tiếp qua bước 2–4 ở trên (§7.4).

**Lát cắt dọc ưu tiên (đã duyệt 2026-10-25, §16):** PDF tài liệu học → Vault → câu lý thuyết MCQ → màn luyện tập. Các bước: nhập và xác nhận PDF (§6.5) → xác nhận cặp khái niệm, do LLM đề xuất nếu có, hoặc người dùng tự bôi (§6.6) → `POST /v1/blueprints` chỉ có `theory_slots` (§6.7) → `POST …/twins` → màn luyện tập. Màn luyện tập cho người học chọn phương án rồi mới hiện đáp án, trích dẫn và badge misconception; đúng hay sai là phép so khoá đã chọn với `correct_key` do backend trả, frontend không tính số nào (I1). Lát cắt này không cần sandbox, template hay LLM. Câu tự luận (nếu làm, §6.9) dùng cùng màn: người học gõ câu trả lời → `POST /v1/twins/{twin_id}/essay-grade` → hiện "khớp x/y ý" và các ý còn thiếu.

## 5. Data Contracts

Nguồn chân lý là Pydantic; khối mã ở §5.1–§5.6 phản ánh đúng code ở commit chốt spec. Kiểu TypeScript tương ứng nằm trong `holocron/frontend/src/types/holocron.ts` với tên field snake_case giữ nguyên. Field Python `X | None` có default `None` luôn có mặt trong JSON response với giá trị `null`.

**Contract theo ADR-005…008 (§5.7–§5.13, viết 2026-10-24).** Các mục này mô tả contract mục tiêu. Model đã có trong code được chép nguyên văn; chỗ khác code ghi rõ "đổi theo ADR-00x" (đã duyệt cùng ADR) hoặc "đề xuất (chờ duyệt)" (vượt ra ngoài ADR). Mọi model mới kế thừa `StrictModel` (`extra="forbid"`, cắt khoảng trắng). Mỗi field ghi nguồn dữ liệu trong comment: *code* (tính tất định), *sandbox* (I1), *người dùng*, *LLM đề xuất* (luôn qua một cổng kiểm), hoặc *engine đọc* (pypdfium2, Tesseract, RapidOCR, VLM).

**Duyệt ngày 2026-10-25.** 14 trong 15 điểm từng ghi "đề xuất (chờ duyệt)" ở v0.5 đã được duyệt và đổi nhãn tại chỗ thành "đã duyệt 2026-10-25"; danh sách ở §17 D7. Điểm thứ 15 ("503 khi thiếu key LLM") bị thay bằng chế độ khả năng (§11.4). Khi một khối được duyệt, các ghi chú "đề xuất" nội dòng bên trong khối đó cũng được duyệt. Ghi chú "đề xuất" nội dòng nằm ngoài các khối này vẫn chờ duyệt (§17 D8). Phần viết mới ở v0.6 ghi rõ "đã duyệt 2026-10-25" hoặc "đề xuất (chờ duyệt)". Ở v0.7, các điểm người dùng duyệt tối 2026-10-25 được đổi nhãn tại chỗ, ghi "(duyệt 2026-10-25)" hoặc "đã duyệt 2026-10-25"; phần còn lại giữ nhãn cũ (§17 D7, D8). Đợt duyệt 00:25 ngày 2026-10-26 đổi nhãn tại chỗ, ghi "(duyệt 2026-10-26)"; danh sách ở §17 D8. Đợt duyệt chiều 2026-10-26 ghi "(duyệt 2026-10-26 chiều)"; chỗ người dùng chỉ duyệt hướng và spec phải thêm chi tiết thì ghi "đề xuất (chi tiết), chờ duyệt"; danh sách ở §17 D8.

**Đánh số trang (đã duyệt 2026-10-25).** Mọi contract (API, JSON, SQLite) dùng `page_number` ≥ 1: `ExtractedTable`, `Citation`, `KnowledgeChunk`, `PageExtraction`, `IngestionItem`. `page_index` 0-based của pypdfium2 chỉ dùng bên trong worker đọc PDF; quy đổi `page_number = page_index + 1` tại ranh giới worker (ADR-008 mục 3, mục 13).

### 5.1 `ProcessItem` & `CPUSchedulingProblem` (`domain/schemas.py`)

```python
class SchedulingAlgorithm(str, Enum):
    RR = "RR"
    SJF = "SJF"

class ProcessItem(StrictModel):            # extra="forbid", strip whitespace
    name: str = Field(min_length=1, max_length=32)
    arrival_time: int = Field(ge=0, le=200)
    burst_time: int = Field(ge=1, le=100)

class CPUSchedulingProblem(StrictModel):
    algorithm: SchedulingAlgorithm
    processes: list[ProcessItem] = Field(min_length=1, max_length=10)
    quantum: int | None = Field(default=None, ge=1, le=20)
    def to_template_params(self) -> dict[str, list[str] | list[int] | int]: ...
```

- RR PHẢI có `quantum`; SJF KHÔNG ĐƯỢC có `quantum`.
- Tên tiến trình PHẢI duy nhất (so sánh không phân biệt hoa thường, đã chuẩn hoá khoảng trắng).
- `to_template_params()` trả `{"names", "arrivals", "bursts"}` và thêm `"quantum"` khi có — đúng dạng `params` mà template §8 nhận.

### 5.2 Bảng trích xuất (`services/ingestion_gate.py`)

```python
class ExtractedCell(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    verified_by_human: bool = False

class ExtractedTable(BaseModel):
    table_id: str
    page_number: int = Field(ge=1)
    version: int = Field(default=1, ge=1)     # tăng 1 sau mỗi PATCH
    headers: list[str]
    cells: list[ExtractedCell]                # toạ độ duy nhất, col < len(headers)
```

### 5.3 `GateIssue` (vai trò *CellIssue*) & `GateReport`

Model mang tên `GateIssue` vì không phải issue nào cũng thuộc một ô: `LATEX_UNBALANCED` và `SELF_SOLVE_MISMATCH` có `row = col = null`. Issue cấp ô (vai trò *CellIssue*) là issue có cả `row` và `col` khác `null`; Frontend PHẢI dùng cặp này để tô ô.

```python
class Severity(str, Enum):
    INFO = "info"; WARNING = "warning"; BLOCKING = "blocking"

class GateIssue(BaseModel):
    code: Literal["DUAL_PASS_MISMATCH", "LOW_CONFIDENCE", "HEADER_MISMATCH", "NOT_A_NUMBER",
                  "OUT_OF_RANGE", "DUPLICATE_LABEL", "MISSING_CELL", "NOT_IN_TEXT_LAYER",
                  "SELF_SOLVE_MISMATCH", "LATEX_UNBALANCED",
                  "CROSS_CHECK_UNAVAILABLE"]       # thêm theo ADR-008 mục 11; chưa có trong code (§7.4)
    severity: Severity
    message_vi: str
    row: int | None = None
    col: int | None = None
    alternatives: list[str] = []              # các giá trị hai lượt đọc ra (DUAL_PASS_MISMATCH)

class GateStatus(str, Enum):
    AUTO_PASS = "AUTO_PASS"; NEEDS_REVIEW = "NEEDS_REVIEW"; BLOCKED = "BLOCKED"

class GateReport(BaseModel):
    table_id: str
    table_version: int                        # = ExtractedTable.version tại thời điểm tính
    status: GateStatus                        # BLOCKED nếu có ≥1 BLOCKING; NEEDS_REVIEW nếu chỉ có WARNING
    issues: list[GateIssue]
```

### 5.4 Tài liệu tải lên & Ingestion Item

```python
MediaType = Literal["image/png", "image/jpeg", "image/webp", "application/pdf"]

class UploadedDocument(BaseModel):           # frozen
    document_id: str                         # "doc_" + sha256[:16] — cùng nội dung ⇒ cùng id
    sha256: str
    filename: str
    media_type: MediaType                    # xác định bằng magic bytes, không theo Content-Type
    size_bytes: int
    uploaded_at: datetime

class SolverInputs(BaseModel):               # extra="allow"
    quantum: int | None = None               # gt=0; gửi null tường minh ⇒ 422

class IngestionItem(BaseModel):
    item_id: str
    source_doc_id: str
    page_number: int
    topic_key: str
    stem_latex: str
    options: dict[OptionKey, str]
    table_primary: ExtractedTable            # lượt A; mọi PATCH áp lên bảng này
    table_secondary: ExtractedTable | None   # lượt B; dùng cho DUAL_PASS_MISMATCH
    page_text_layer: str | None
    solver_inputs: SolverInputs
    non_cloneable: bool
    non_cloneable_reason: str | None
    problem: CPUSchedulingProblem | None     # null khi bảng chưa chuẩn hoá được
    self_solve: SelfSolveResult | None
    report: GateReport | None
    audits: list[PatchAudit]
    approved: bool

class UploadResponse(BaseModel):
    document: UploadedDocument
    item: IngestionItem                      # item.report là GateReport của lần quét đầu
```

**Thay đổi đã lên kế hoạch cho `IngestionItem` (chưa có trong code):**
- Đổi theo ADR-005 bước 2: `problem: CPUSchedulingProblem | None` thay bằng `params: dict[str, Any] | None` (nguồn: *code*, chuyển bảng và `solver_inputs` qua `input_schema` của template đã kiểm chứng, §5.10; `null` khi chưa chuyển được). `SolverInputs.quantum` thay bằng các tham số vô hướng khai báo trong `input_schema.scalars`; `solver_inputs` thành `dict[str, int | float]` được kiểm theo `input_schema`. Form field `quantum` của §6.1 bị bỏ cùng bước này.
- Đã duyệt 2026-10-25: `cross_check: Literal["ok", "mismatch", "unavailable", "not_applicable"] | None = None` (nguồn: *code*, chép từ `PageExtraction.cross_check.status` khi item được tạo từ trang PDF, §7.4). `null` với item tạo qua §6.1 hoặc `POST /v1/ingestion/items`, nên hành vi Gate của các luồng đó không đổi.
- Quyết định (a), duyệt 2026-10-25: bắt buộc truyền `page_source` khi nối API bộ nhập tài liệu với Ingestion Gate, cho mọi item đến từ trang PDF (§6.5, §7.4).
  - Cơ chế bắt buộc (đề xuất, suy ra từ quyết định (a)): model nội bộ mà service dùng để tạo item từ một trang PDF (sau `POST …/pages/{page_number}/items`, §6.5; không phải `PageItemCreate`, vì người dùng không gửi field này) có field `page_source: TierUsed` **không có giá trị mặc định**, nên thiếu `page_source` là lỗi kiểu (mypy `--strict`) và lỗi validate của Pydantic, không chỉ là quy ước. Nguồn: *code*, chép từ `PageExtraction.tier_used` của trang tạo item (§5.7).
  - Field trên `IngestionItem` (đề xuất, suy ra từ quyết định (a)): `page_source: TierUsed | None = None`. Item tạo qua §6.1 hoặc `POST /v1/ingestion/items` có `page_source = null`.
  - Validator (đề xuất, suy ra từ quyết định (a)): `page_source` khác `null` và khác `"text_layer"` mà `cross_check = null` thì code đặt `cross_check = "unavailable"`, để item đến từ trang không dùng lớp chữ không bao giờ bỏ qua cảnh báo đối chiếu chéo vì thiếu trạng thái. `cross_check = null` giữ nghĩa cũ (item không đến từ PDF).
- Đã duyệt 2026-10-25: `labels: ItemLabels` cho các nhãn mà `POST /v1/blueprints` cần (§5.9), sửa qua `PATCH /v1/ingestion/items/{id}/labels` (§6.7):

```python
class ItemLabels(StrictModel):
    subject_code: str | None = Field(default=None, pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")
        # người dùng; mặc định chép từ DocumentMeta.subject_code khi item đến từ bộ nhập (§6.5)
    chapter_code: str | None = Field(default=None, pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
        # LLM đề xuất trên văn bản trang (khi có LLM, §11.4), hoặc chép từ chunk Vault mà VaultCitationProvider chọn cho topic_key; người dùng sửa
    cognitive_level: CognitiveLevel = CognitiveLevel.APPLY
        # LLM đề xuất khi có LLM; mặc định "apply" khi không có; người dùng sửa
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
        # độ tin cậy LLM trả cho chapter_code/cognitive_level; = 1.0 khi người dùng đã xác nhận hoặc tự nhập
    confirmed_by_human: bool = False
```

### 5.5 `TwinQuestion` (`domain/schemas.py`)

```python
class TwinQuestion(StrictModel):
    twin_id: str
    blueprint_id: str
    slot_index: int
    template_id: str
    topic_key: str
    seed: int                                # 0 ≤ seed < 2**63
    format: QuestionFormat                   # MCQ4 | NUMERICAL
    stem: str                                # Markdown + LaTeX do render_stem() sinh
    options: dict[OptionKey, str] | None     # MCQ4: đủ A–D, 4 nội dung khác nhau
    correct_key: OptionKey | None
    numerical_answer: NumericalAnswer | None # NUMERICAL: bắt buộc
    distractor_to_misconception_map: dict[OptionKey, MisconceptionRef]   # badge trên UI
    solution_explanation_vi: str
    citation: Citation
    trace: GenerationTrace                   # sandbox_duration_ms, llm_attempts, seed_rerolls, template_sha256, provenance
    review_status: ReviewStatus
    generated_at: datetime
    gantt: list[GanttSegment]                # BỔ SUNG T2-N3 — xem §5.6
```

Quy tắc MCQ4: `correct_key ∉ distractor_to_misconception_map` và map phủ đúng 3 phương án còn lại (I2). NUMERICAL: không có `options`/`correct_key`, bắt buộc `numerical_answer.tolerance_abs`.

Thay đổi theo ADR-005 (`question_kind`, `artifacts` thay `gantt`) và ADR-007 (câu lý thuyết) ở §5.11. `QuestionFormat` thêm `ESSAY` và `TwinQuestion` thêm field `essay` (đã duyệt 2026-10-25; chi tiết đề xuất ở §5.14).

### 5.6 Bổ sung chốt cho Tuần 2 Ngày 3 (`GanttSegment`, `TwinQuestion.gantt`, `TwinBatchRequest` và `TwinBatch` đã có trong code)

```python
class GanttSegment(StrictModel):
    process: str = Field(min_length=1, max_length=32)   # trùng ProcessItem.name
    start: int = Field(ge=0)
    end: int                                            # PHẢI > start

class TwinBatchRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    format: QuestionFormat = QuestionFormat.MCQ4
    base_seed: int | None = Field(default=None, ge=0, lt=2**63)

class TwinBatch(BaseModel):
    blueprint_id: str
    base_seed: int
    twins: list[TwinQuestion]
    review_tickets: list[ReviewTicket]       # slot không sinh được (services/pipeline.py)
```

- `TwinQuestion.gantt` có default `[]`; với topic CPU Scheduling PHẢI không rỗng. Các segment PHẢI sắp theo `start`, không chồng lấn; khoảng CPU rảnh không có segment.
- `gantt` PHẢI lấy từ khoá `"gantt"` của `solve()` (§8.1), KHÔNG ĐƯỢC tính lại ngoài sandbox (I1).
- Khi thêm vào code, `holocron/frontend/src/types/holocron.ts` PHẢI cập nhật trong cùng PR.
- `BlueprintCreate` bản v0.3 (chỉ có `item_ids`) được thay bằng bản ở §5.9. `GanttSegment` và `TwinQuestion.gantt` bị thay bằng `artifacts` theo ADR-005 bước 3 (§5.11). Đã duyệt 2026-10-25 cho `TwinBatch`: thêm `batch_id` và `created_at` để đọc lại batch (§6.7).
- `TwinBatchRequest.format` (duyệt 2026-10-26 chiều): chỉ ghi đè hình thức của slot tính toán và chỉ nhận `MCQ4` hoặc `NUMERICAL`; gửi `ESSAY` trả 422. Hình thức của slot lý thuyết lấy từ `TheorySlotSpec.format` (§5.9, §5.14), không bị `format` của request ghi đè.

### 5.7 Bộ nhập PDF: `PageExtraction` (ADR-008 mục 13)

Nguồn: worker đọc PDF (ADR-008 mục 3) trả JSON theo từng trang; tiến trình cha validate bằng các model dưới đây. Ngưỡng là cấu hình (ADR-008 mục 12): `TEXT_LAYER_MIN=0.85`, `TEXT_SPARSE_CHARS=32`, `TEXT_SPARSE_IMAGE_MIN=0.40`, `TEXT_FULL_IMAGE_MIN=0.90`, `TEXT_FULL_IMAGE_MAX_CHARS=200` (thêm 2026-10-30, người dùng duyệt: điều kiện (d), lớp chữ sạch VÀ `non_ws < TEXT_FULL_IMAGE_MAX_CHARS` VÀ `image_area_ratio ≥ TEXT_FULL_IMAGE_MIN` thì OCR cả trang, lý do `image_covers_page`; lớp chữ ≥ 200 ký tự trên ảnh phủ kín giữ lớp chữ; thứ tự xét rỗng, rác, thưa, `image_covers_page`; hai ngưỡng đầu thêm 2026-10-29, người dùng duyệt: trang ảnh có `non_ws < TEXT_SPARSE_CHARS` VÀ `image_area_ratio ≥ TEXT_SPARSE_IMAGE_MIN` thì OCR cả trang; xem công thức `text_score = clean_char_ratio × valid_word_ratio` và luật chọn tầng ở ADR-008 mục 6, sửa đổi 2026-10-29), `TEXT_SCORE_MIN=0.80`, `OCR_SCORE_MIN=0.80`, `LINE_CONF_MIN=0.70`, `COVERAGE_MIN=0.85`, `FIGURE_INK_MIN=0.10`, `CROSS_LINE_CER_WARN=0.05`. **Ảnh template (thêm 2026-10-30, người dùng duyệt; ADR-008 mục 6, 9):** độ phủ ảnh dùng cho điều kiện (c), điều kiện (d) và phần ảnh của `has_figure` trên PDF số chỉ tính ảnh KHÔNG phải template. Ảnh template là ảnh nhúng (khoá: SHA-256 dữ liệu thô của luồng ảnh (chưa giải mã), kích thước điểm ảnh, bộ lọc, hộp bao chuẩn hoá làm tròn theo `TEMPLATE_IMAGE_BBOX_STEP=0.01`; không dùng bits-per-pixel, field `ImageKey.bits_per_pixel` luôn 0 và không tham gia so khớp; dung sai vị trí ±1 ô đối xứng, hai hộp lệch ≤ một bước là cùng nhóm kể cả khi vắt qua biên ô, không bắc cầu) có mặt trên ≥ `TEMPLATE_IMAGE_MIN_PAGES=3` trang VÀ ≥ `TEMPLATE_MIN_PAGE_RATIO` tổng số trang (đổi tên 2026-10-31 từ `TEMPLATE_IMAGE_MIN_PAGE_RATIO`; mặc định 0.40, người dùng duyệt 2026-11-01 theo số đo GĐ0 ở ADR-008 "Kết quả đo"); tài liệu 1–2 trang không có ảnh template; một hàm duy nhất `find_template_images` xác định (khung VLM dùng lại; từ 2026-10-31 hàm này xác định cả đường vẽ template và dòng header/footer, mục GĐ0 dưới đây). Ảnh nền phủ kín vẫn bị trừ nếu là template; `path_area_ratio` toàn phần không còn dùng cho cờ hình từ 2026-10-31 (GĐ0); lớp chữ rỗng hoặc rác vẫn OCR bất kể ảnh. Lượt quét ảnh một lần ở worker trước trang đầu, không render, có hạn và trần riêng (`TEMPLATE_SCAN_TIMEOUT_S=20.0`, `TEMPLATE_SCAN_MAX_IMAGES_PER_PAGE=256`, `TEMPLATE_SCAN_MAX_IMAGES=20000`, `TEMPLATE_SCAN_MAX_IMAGE_BYTES=16 MiB`, `TEMPLATE_SCAN_MAX_BYTES=512 MiB`); vượt trần, quá hạn hoặc lỗi thì không ảnh nào là template (hành vi cũ). Cờ hình trên PDF số: luật hiện hành ở mục "Khử nhiễu trang và vai trò trang (GĐ0)" dưới đây (sửa đổi 2026-10-31, người dùng duyệt). Luật 2026-10-30 (nhánh đường vẽ tính mọi ảnh, kể cả template, và `path_area_ratio` toàn phần) đã thay 2026-10-31; câu "Không đổi `ReviewReason` hay contract JSON" của 2026-10-30 cũng đã thay 2026-10-31, vì GĐ0 thêm `ReviewReason`, cờ dòng và field. Cùng model cấu hình có `OCR_CHARSET_ALLOWED_MISSING` (mặc định `Ỵ`, `ỵ`; duyệt 2026-10-25, ADR-008 mục 14): danh sách chữ tiếng Việt được phép thiếu trong unicharset của engine chính, kèm cảnh báo. Danh sách chỉ được chứa các chữ hiếm đã duyệt (hiện `Ỵ`, `ỵ`); mở rộng danh sách cần người dùng duyệt riêng. Đây là field có giá trị mặc định ở `infrastructure/`, không phải biến môi trường (ADR-008 mục 12). Không có ràng buộc `TEXT_LAYER_MIN ≥ TEXT_SCORE_MIN` giữa hai ngưỡng (duyệt 2026-10-25): cấu hình được phép đặt `TEXT_LAYER_MIN` thấp hơn `TEXT_SCORE_MIN`, khi đó trang dùng lớp chữ với `text_score` nằm giữa hai ngưỡng nhận lý do `LOW_TEXT_SCORE`.

```python
LineEngine = Literal["text_layer", "tesseract", "vlm", "human"]
    # engine cho ra `text` của dòng; "human": dòng người dùng gõ thêm qua PATCH …/lines (§6.5) (duyệt 2026-10-26
    # chiều). Dòng người dùng sửa GIỮ engine gốc, không đổi thành "human"; dòng đã sửa được nhận biết qua
    # edited_by_human (duyệt 2026-10-26 chiều). Trang failed không sửa được trong MVP (duyệt 2026-10-26)
TierUsed = Literal["text_layer", "ocr", "layout", "vlm"] # tầng 1, 2, 3, 4 (ADR-008 mục 6, 9)
LineFlag = Literal[
    "LOW_LINE_CONF", "TOKEN_MISMATCH", "HIGH_LINE_CER",
    # thêm 2026-10-31 (GĐ0, người dùng duyệt; mục "Khử nhiễu trang" dưới đây), chỉ gắn cho dòng engine "text_layer":
    "LOW_TEXT_CONTRAST",        # 0B(1): tương phản chữ/nền nằm giữa HIDDEN_TEXT_DROP_MAX và HIDDEN_TEXT_KEEP_MIN
    "TEXT_OCCLUDED",            # 0B(2): dòng có chữ bị ảnh hoặc PATH tô đặc vẽ sau che ≥ OCCLUSION_MIN_COVER hộp chữ
    "FONT_ENCODING_SUSPECT",    # 0B(3): cổng glyph
]
PageRole = Literal[                               # thêm 2026-10-31 (GĐ0, 0D)
    "content", "cover_or_course_nav", "chapter_nav", "build_step", "parallel_visual_series", "exercise",
]
ReviewReason = Literal[
    "LOW_TEXT_SCORE",           # trang dùng lớp chữ mà text_score < TEXT_SCORE_MIN (mục 6; duyệt 2026-10-25)
    "LOW_OCR_SCORE",            # ocr_score < OCR_SCORE_MIN (mục 6)
    "LOW_LINE_COVERAGE",        # coverage_ratio < COVERAGE_MIN (mục 10)
    "HIGH_INK_OUTSIDE_TEXT",    # không có lượt thứ hai và ink_outside_text_ratio ≥ FIGURE_INK_MIN (mục 10)
    "FIGURE_ON_SCAN",           # has_figure trên trang OCR (mục 9)
    "FORMULA_ON_SCAN",          # has_formula trên trang OCR (mục 9)
    "TIER3_UNAVAILABLE",        # trang lẽ ra lên tầng 3 nhưng tầng 3 chưa có (mục 9): bảng, công thức. Từ 2026-10-31
                                # hình trên PDF số không còn sinh lý do này (luật 0C, mục GĐ0 dưới đây)
    "LOW_CONF_LINES",           # có dòng conf < LINE_CONF_MIN, nghi chữ viết tay hoặc chữ mờ (mục 10)
    "TOKEN_MISMATCH",           # cross_check.status = "mismatch" (mục 11)
    "CROSS_CHECK_UNAVAILABLE",  # trang OCR không có lượt đọc thứ hai (mục 11)
    "HIGH_PAGE_CER",            # CER văn bản giữa hai lượt ≥ 0.15; ngưỡng chưa xác minh (mục 11)
    "OCR_ENGINE_MISSING",       # thiếu Tesseract/vie.traineddata, trang đã được đọc bằng VLM (mục 8)
    # thêm 2026-10-31 (GĐ0, người dùng duyệt; khai báo SAU các lý do cũ để thứ tự sắp của trang cũ không đổi):
    "LOW_TEXT_CONTRAST",        # có dòng cờ LOW_TEXT_CONTRAST (0B(1))
    "TEXT_OCCLUDED",            # có dòng cờ TEXT_OCCLUDED (0B(2))
    "FONT_ENCODING_SUSPECT",    # có dòng cờ FONT_ENCODING_SUSPECT (0B(3))
    "IMAGE_NEEDS_READING",      # luật 0C: < FIGURE_TEXT_MIN_TOKENS token VÀ ảnh nội dung ≥ IMAGE_READ_AREA_MIN
    "PAGE_ROLE_UNCERTAIN",      # 0D: vùng giữa của luật build_step (dHash BUILD_STEP_DHASH_SAME_MAX+1 .. PARALLEL_SERIES_DHASH_MIN-1)
    "DENOISE_UNAVAILABLE",      # lượt quét cấp tài liệu không `ok` và trang giữ lớp chữ có has_figure (đề xuất, chờ duyệt)
    # thêm 2026-11-01 (ADR-010 mục 6; duyệt 2026-11-01 (ADR-010), tên là đề xuất suy ra), khai báo SAU mọi lý do cũ:
    "LAYOUT_FAILED",            # bộ dựng khối lỗi/vượt trần, khối không phải paragraph vượt max_length, trang lớp chữ
                                # thiếu PageGeometry (tài liệu có layout_version), hoặc ExtractionLedger không cân (§5.15): khối của trang bị bỏ,
                                # trang dùng đường dòng (build_chunks, §12.1); tính lại mỗi lần trang được dựng lại sau
                                # PATCH …/lines, nên có thể được thêm hoặc bỏ (§6.5)
]
FailureReason = Literal["PAGE_TIMEOUT", "PAGE_ERROR", "OCR_ENGINE_MISSING", "TOO_MANY_LINES"]
# TOO_MANY_LINES (duyệt 2026-10-28, câu 18 (a)): trang có số dòng có chữ vượt giới hạn trang của Vault (5000, §5.13)
# được ghi `failed` ngay lúc đọc, lines rỗng (code: `_within_vault_lines`); các trang khác xác nhận bình thường.

class ExtractedLine(StrictModel):
    line_index: int = Field(ge=0)                 # code: thứ tự đọc trong trang, 0-based như ExtractedCell.row
    text: str = Field(max_length=2000)            # engine đọc, chuẩn hoá NFC; người dùng sửa qua §6.5
    bbox: tuple[float, float, float, float] | None
        # engine đọc; (x0, y0, x1, y1) chia theo khổ trang, ∈ [0, 1], gốc trên-trái (đề xuất); null với dòng người dùng gõ thêm
    conf: float = Field(ge=0.0, le=1.0)           # Tesseract: trung bình cột conf/100 của các từ, trọng số theo số ký tự;
                                                  # text_layer: = text_score của trang (đề xuất); vlm: 0.0 (VLM không trả điểm, đề xuất);
                                                  # dòng gõ thêm (engine "human"): 1.0 (duyệt 2026-10-26 chiều).
                                                  # Dòng người dùng sửa GIỮ conf gốc của engine, không ghi đè thành 1.0
                                                  # (duyệt 2026-10-26 chiều). Dòng edited_by_human = true không tính là
                                                  # conf thấp, bất kể conf (duyệt 2026-10-26, §6.5)
    engine: LineEngine
    flags: list[LineFlag] = []                    # code
    edited_by_human: bool = False
        # code: true sau khi người dùng sửa dòng qua PATCH …/lines (§6.5), và true với dòng gõ thêm (duyệt 2026-10-26 chiều)
    is_header_footer: bool = False
        # code (thêm 2026-10-31, GĐ0 0B(5)): dòng engine "text_layer" khớp một khoá TEXT template (header/footer) của tài
        # liệu. Dòng vẫn nằm trong `lines` (tương thích, line_index liên tục) nhưng KHÔNG vào văn bản xác nhận hay chunk,
        # không tính là "dòng có chữ" (§5.7 "Chốt trạng thái") và không đếm vào token của 0C, 0D. Sửa dòng qua
        # PATCH …/lines giữ nguyên giá trị (đề xuất suy ra, chờ duyệt). Dòng gõ thêm luôn false

class PageImage(StrictModel):                     # thêm 2026-10-31 (GĐ0 0A, 0D); một ảnh nội dung (không-template) của trang
    bbox: tuple[float, float, float, float]       # worker: hộp thật qua form XObject (0A), chuẩn hoá [0, 1], gốc trên-trái
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
        # worker: SHA-256 dữ liệu thô của luồng ảnh (cùng digest của ImageKey); null khi ảnh vượt TEMPLATE_SCAN_MAX_IMAGE_BYTES
    dhash: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
        # worker: dHash 64 bit (hex) của vùng `bbox` trên ảnh render HIDDEN_TEXT_RENDER_DPI (ảnh xám 9×8, so cột kề nhau);
        # null khi trang không render được ở bước này

class PageRef(StrictModel):                       # thêm 2026-10-31 (GĐ0 0D)
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)

class PageRoleEvidence(StrictModel):              # thêm 2026-10-31 (GĐ0 0D); code: lý do của page_role / bỏ mềm / cờ vùng giữa
    rule: Literal["cover_or_course_nav", "chapter_nav", "build_step", "parallel_visual_series", "build_step_uncertain"]
    related_pages: list[PageRef] = Field(min_length=1, max_length=300)
        # trang so sánh: trang tài liệu khác (cover), các trang cùng cụm (chapter_nav), trang i+1 (build_step,
        # build_step_uncertain), trang kề cùng chuỗi (parallel_visual_series)
    jaccard: float | None = Field(default=None, ge=0.0, le=1.0)   # độ trùng token với trang liên quan gần nhất
    dhash_distance: int | None = Field(default=None, ge=0, le=64) # build_step*, parallel_visual_series: khoảng cách lớn nhất

class TokenMismatch(StrictModel):
    line_index: int = Field(ge=0)
    primary_value: str                            # token của lượt chính (regex 0x[0-9A-F]+|#?-?\d+, NFC)
    secondary_value: str | None                   # token RapidOCR ghép theo bbox; null khi lượt thứ hai không có token ở đó

class CrossCheck(StrictModel):
    status: Literal["ok", "mismatch", "unavailable", "not_applicable"]
        # code. "not_applicable" (đề xuất): trang đọc bằng lớp chữ (tầng 1), không có OCR để đối chiếu
    secondary_engine: Literal["rapidocr"] | None  # cấu hình HOLOCRON_PDF_OCR_SECONDARY; null khi unavailable/not_applicable
    mismatches: list[TokenMismatch] = []          # không rỗng ⇔ status = "mismatch"

class PageExtraction(StrictModel):
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)                # = page_index + 1
    version: int = Field(default=1, ge=1)         # code: tăng 1 mỗi lần sửa dòng (optimistic locking)
    status: Literal["pending", "ok", "needs_review", "failed"]
        # code. "pending" (đề xuất): chưa đọc xong; needs_review ⇔ review_reasons không rỗng
    tier_used: TierUsed | None                    # code (mục 6, 9); null khi pending hoặc failed
    text_score: float | None = Field(ge=0.0, le=1.0)
        # tầng 1 (mục 6); null khi pending. "null khi trang failed do PAGE_TIMEOUT": mô tả hành vi code hiện tại,
        # đề xuất sửa câu chữ, chờ duyệt
    ocr_score: float | None = Field(default=None, ge=0.0, le=1.0) # tầng 2; null khi trang không OCR
    coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
        # min(n_chính, n_phụ) / max(n_chính, n_phụ), n là số dòng; null khi không có lượt thứ hai (đã duyệt 2026-10-24)
    ink_outside_text_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
        # mẫu số: tổng điểm ảnh mực sau nhị phân hoá (đã duyệt 2026-10-24); null khi trang không OCR
    has_figure: bool = False                      # code: cờ trang (ADR-008 mục 6, 9)
    has_table: bool = False                       # code: cờ trang; KHÔNG lấy thẳng từ pdfplumber.find_tables (ghi chú tầng 3 dưới đây)
    has_formula: bool = False                     # code: cờ trang (ADR-008 mục 6, 9)
    lines: list[ExtractedLine] = []               # trang OCR: text từ lượt chính; RapidOCR chỉ góp token để đối chiếu
    tables: list[ExtractedTable] = []             # đề xuất: bảng của tầng 3 (§5.2); rỗng khi tầng 3 chưa có
    cross_check: CrossCheck
    review_reasons: list[ReviewReason] = []       # code; sắp theo thứ tự khai báo, không trùng
    failure_reason: FailureReason | None = None   # bắt buộc khi status = "failed", ngược lại null
    confirmed: bool = False                       # người dùng (§6.5)
    confirmed_at: datetime | None = None
    # --- thêm 2026-10-31 (GĐ0, người dùng duyệt; mục "Khử nhiễu trang và vai trò trang" dưới đây). Mọi field có mặc định,
    # nên JSON trang lưu trước GĐ0 vẫn validate được (page_role = "content", không bỏ mềm) mà không cần migration (§5.13).
    own_images: list[PageImage] = Field(default=[], max_length=256)
        # worker: ảnh không-template, không-nền của trang (0A), theo thứ tự vẽ; trần = TEMPLATE_SCAN_MAX_IMAGES_PER_PAGE
    hidden_chars_dropped: int = Field(default=0, ge=0)
        # worker: số ký tự lớp chữ bị loại vì chữ ẩn (0B(1)); ký tự bị loại không có trong `lines`
    scan_text_layer: bool = False
        # worker: trang được nhận là lớp OCR của PDF scan (0B(1)): không lọc chữ ẩn, không xét TEXT_OCCLUDED
    content_token_count: int | None = Field(default=None, ge=0)
        # code: số token (định nghĩa token ở 0C) của lớp chữ sau lọc chữ ẩn, không tính dòng header/footer; có cả ở trang
        # đi OCR (đếm trên lớp chữ trước khi OCR); null khi trang pending, hoặc failed trước khi đọc được lớp chữ
    page_role: PageRole = "content"               # code (service, 0D), sau khi đọc xong tài liệu; "exercise" dành cho LLM, chưa sinh ở GĐ0–GĐ1
    role_evidence: PageRoleEvidence | None = None # code (0D); null ⇔ page_role = "content" và không có PAGE_ROLE_UNCERTAIN
    dropped: bool = False
        # code (0D): bỏ mềm. true chỉ khi page_role ∈ {cover_or_course_nav, chapter_nav, build_step}; trang bỏ mềm không vào
        # chunk, không tính needs_review, không xác nhận được cho tới khi hoàn tác (§6.5 POST …/restore)
    restored: bool = False
        # người dùng (§6.5 POST …/restore): true sau khi hoàn tác bỏ mềm; trang restored KHÔNG BAO GIỜ bị bỏ mềm lại tự động
    # --- thêm 2026-10-31 (GĐ1 1B, tự xác nhận cấp trang; ADR-008 mục 7, duyệt 2026-10-31). Có mặc định, không cần migration.
    confirmed_by: Literal["user", "auto"] | None = None
        # code: "user" khi xác nhận qua POST …/confirm (từng trang hoặc theo lô); "auto" khi tự xác nhận (§5.7 "Tự xác nhận
        # cấp trang"); null khi chưa xác nhận. Trang xác nhận trước GĐ1 có confirmed = true, confirmed_by = null (coi như "user")
    confirm_rule: str | None = Field(default=None, pattern=r"^[a-z0-9_]+_v[0-9]+$")
        # code: tên và phiên bản luật tự xác nhận đã áp (hiện "page_clean_v1"); khác null ⇔ confirmed_by = "auto"
    auto_confirm_undone: bool = False
        # người dùng (§6.5 POST …/unconfirm): true sau khi hoàn tác tự xác nhận; trang này KHÔNG BAO GIỜ tự xác nhận lại
```

- Validator: `status = "needs_review"` ⇔ `review_reasons` không rỗng và trang chưa `failed`; `failure_reason` có giá trị ⇔ `status = "failed"`; `line_index` liên tục `0..n-1`; mỗi `TokenMismatch.line_index` trỏ tới một dòng có cờ `TOKEN_MISMATCH`.
- Validator thêm 2026-10-31 (GĐ0; đề xuất suy ra, chờ duyệt): `dropped = true` ⇒ `page_role ∈ {cover_or_course_nav, chapter_nav, build_step}`, `role_evidence.rule = page_role`, `confirmed = false`, `restored = false`, `status ∈ {ok, needs_review}`; `restored = true` ⇒ `dropped = false`; `page_role ∈ {cover_or_course_nav, chapter_nav, build_step, parallel_visual_series}` ⇒ `role_evidence` khác null và `role_evidence.rule = page_role`; `PAGE_ROLE_UNCERTAIN` trong `review_reasons` ⇔ `role_evidence.rule = "build_step_uncertain"` (khi đó `page_role = "content"`); `is_header_footer = true` chỉ ở dòng `engine = "text_layer"`; cờ dòng `LOW_TEXT_CONTRAST`, `TEXT_OCCLUDED`, `FONT_ENCODING_SUSPECT` chỉ ở dòng `engine = "text_layer"`.
- Validator thêm 2026-10-31 (GĐ1 1B; đề xuất suy ra, chờ duyệt): `confirmed = false` ⇒ `confirmed_by = null`; `confirmed_by = "auto"` ⇔ `confirm_rule` khác null; `confirmed_by = "auto"` ⇒ `review_reasons` rỗng, `page_role = "content"`, `dropped = false`; `auto_confirm_undone = true` ⇒ `confirmed_by ≠ "auto"`.
- `status` của trang bỏ mềm vẫn tính như thường từ `review_reasons` (validator trên); "không tính `needs_review`" nghĩa là các bộ đếm (`DocumentImport`, `DocumentSummary`) và điều kiện chốt tài liệu bỏ qua trang `dropped = true`, không phải đổi `status`.
- Trang `failed` vì `OCR_ENGINE_MISSING`: màn hình xem lại hiện hướng dẫn cài Tesseract và `vie.traineddata` (ADR-008 mục 8). Trang `failed` (mọi `failure_reason`) không sửa và không xác nhận được trong MVP (duyệt 2026-10-26, §6.5): người dùng nhập lại file, tức xoá tài liệu (`DELETE /v1/documents/{document_id}`) rồi tải lên lại, vì tải lên cùng nội dung khi tài liệu còn trả bản ghi cũ mà không đọc lại (§6.5 bước 3). Thao tác "đọc lại trang" thay cho việc nhập lại cả file là việc sau MVP (duyệt 2026-10-26 chiều, §1.6).
- Màn hình xem lại cần đúng các dữ liệu trên: ảnh trang (render phía client bằng pdfjs từ file gốc; sau MVP, duyệt 2026-10-26 chiều, §1.6: MVP chỉ hiện văn bản theo dòng, chưa có ảnh trang), `lines[].bbox` để đặt dòng lên ảnh, `flags` và `review_reasons` để tô, `cross_check.mismatches` để hiện hai giá trị. Frontend không tự tính điểm hay ngưỡng nào. Điểm OCR (`text_score`, `ocr_score`, `coverage_ratio`, `conf` của dòng): frontend chỉ định dạng giá trị backend trả để hiển thị, không tính lại (duyệt 2026-10-26 chiều).
- **Ghi chú cho tầng 3 (đo ngày 2026-10-25 trên slide PDF thật):** `pdfplumber.find_tables` báo bảng ở hầu như mọi trang slide, vì khung viền của slide bị nhận nhầm là bảng. Vì vậy `has_table` PHẢI lọc thêm, không lấy thẳng kết quả `find_tables`. Cách lọc (đề xuất, chờ duyệt): bỏ bảng có đúng một ô, và bỏ bảng có khung bao phủ phần lớn diện tích trang; chỉ bật cờ khi bảng còn lại có ít nhất 2 hàng và 2 cột chứa chữ, đồng thời khớp với lưới toạ độ chữ của tầng 1–2 (ADR-008 mục 6). Ngưỡng là cấu hình (ADR-008 mục 12), hiệu chỉnh trên cùng bộ slide đã đo.

**Tài liệu đã nhập** (đã duyệt 2026-10-25; mở rộng `DocumentMeta` ở §5.8):

```python
class PageSummary(StrictModel):
    page_number: int = Field(ge=1)
    status: Literal["pending", "ok", "needs_review", "failed"]
    tier_used: TierUsed | None
    review_reasons: list[ReviewReason]
    confirmed: bool
    page_role: PageRole = "content"               # thêm 2026-10-31 (GĐ0): chép từ PageExtraction.page_role
    dropped: bool = False                         # thêm 2026-10-31 (GĐ0): chép từ PageExtraction.dropped
    confirmed_by: Literal["user", "auto"] | None = None   # thêm 2026-10-31 (GĐ1 1B): chép từ PageExtraction.confirmed_by
        # kho SQLite đọc ba field này bằng json_extract từ cột extraction_json như tier_used, review_reasons (§5.13)

class DocumentImport(StrictModel):
    meta: DocumentMeta                            # §5.8
    filename: str = Field(min_length=1, max_length=255)   # người dùng (tên file tải lên)
    size_bytes: int = Field(ge=1)
    output_language: Literal["vi", "en"] = "vi"   # người dùng (CLAUDE.md §4.3); giá trị "en" là đề xuất
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
        # Sửa đổi 2026-10-31 (GĐ1 1A, duyệt 2026-10-31): luôn có giá trị trong response. Nguồn: người dùng (field form
        # TUỲ CHỌN của POST /v1/documents, hoặc PATCH …/chapter trước khi xác nhận), hoặc code (tên file / chương trống kế
        # tiếp, §5.7 "Chương tự động"); nguồn ghi ở chapter_source.code. Câu cũ dưới đây "field form bắt buộc" đã thay.
        # Lịch sử: người dùng: field form bắt buộc của POST /v1/documents (§6.5); một file = một chương (duyệt 2026-10-26).
        # Chunk sinh ra khi xác nhận dùng giá trị này (§6.5). Đặt field ở DocumentImport (không ở DocumentMeta,
        # vì DocumentMeta dùng chung với upload đề §6.1): đề xuất, chờ duyệt. Đề cương ôn tập trải nhiều chương vẫn
        # nhận một chapter_code cho cả file; chấp nhận cho MVP (duyệt 2026-10-26 chiều), người dùng sửa chương của
        # từng chunk qua PATCH /v1/vault/chunks/{id}/labels (§6.6)
    status: Literal["processing", "review", "confirmed", "failed"]
        # code: processing khi còn trang pending; review khi đọc xong; confirmed khi mọi trang có văn bản đã xác nhận;
        # failed khi worker không mở lại được tài liệu sau khi đã nhận (hiếm, vì §6.5 thử mở trước khi trả 202).
        # Tài liệu failed vẫn chuyển sang confirmed khi mọi trang có văn bản đã xác nhận (duyệt 2026-10-26 chiều;
        # quy tắc chốt trạng thái dưới đây)
    pages: list[PageSummary]                      # đủ page_count phần tử, theo page_number tăng dần
    # --- thêm 2026-10-31 (GĐ0): số đếm tóm tắt do backend đếm từ `pages`; frontend chỉ hiển thị, không tự đếm (I1, §3.3)
    pages_kept: int = Field(ge=0)                 # "lấy": trang không pending, không failed, không dropped
    pages_dropped: int = Field(ge=0)              # "bỏ": trang dropped = true
    pages_needs_review: int = Field(ge=0)         # "cần xem": status = needs_review, không dropped, chưa confirmed
    # --- thêm 2026-10-31 (GĐ1 1A, 1B; duyệt 2026-10-31)
    chapter_title: str | None = Field(default=None, min_length=1, max_length=300)
        # code (header lặp, tên file đã làm sạch, hoặc LLM chọn/làm gọn trong danh sách ứng viên) hoặc người dùng
        # (PATCH …/chapter); null chỉ với tài liệu nhập trước migration 0004 (§5.13)
    chapter_source: ChapterSource                 # code: nguồn của chapter_code và chapter_title
    chapter_flags: list[ChapterFlag] = []         # code; sắp theo thứ tự khai báo, không trùng
    auto_confirm: bool = True                     # người dùng: field form của POST /v1/documents (mặc định bật, §6.5)
    pages_auto_confirmed: int = Field(ge=0)
        # code (thêm 2026-10-31, GĐ1 1B): số trang confirmed_by = "auto" (đếm từ pages; frontend chỉ hiển thị, I1)
    pages_kept_unconfirmed: int = Field(ge=0)
        # code (thêm 2026-10-31, GĐ1 1B): trang được giữ nhưng chưa xác nhận và không thuộc pages_needs_review: không
        # pending/failed/dropped, chưa confirmed, status = "ok" (ví dụ parallel_visual_series, trang restored, trang đã hoàn
        # tác tự xác nhận, trang không qua luật độ tin cậy nguồn); đây là các trang "Chấp nhận" sẽ xác nhận theo lô
    # --- thêm 2026-11-01 (T1e, màn kết quả nhập; người dùng duyệt hướng 2026-11-01; tên field là đề xuất suy ra, chờ xác
    # nhận). Mọi field dưới đây do backend tính lúc dựng response; KHÔNG lưu, không cột, không migration (§5.13). Frontend
    # chỉ hiển thị, không tự đếm từ `pages` (I1, §3.3)
    pages_failed: int = Field(default=0, ge=0)
        # code: số trang status = "failed" (cùng định nghĩa DocumentSummary.pages_failed) (thêm suy ra, chờ xác nhận)
    review_reason_counts: list[ReviewReasonCount] = []
        # code: số trang theo từng ReviewReason, chỉ xét các trang được đếm vào pages_needs_review (status = needs_review,
        # không dropped, chưa confirmed); một trang mang nhiều lý do được đếm ở mỗi lý do. Chỉ có lý do với count ≥ 1;
        # sắp theo thứ tự khai báo của ReviewReason (ổn định)
    dropped_role_counts: list[DroppedRoleCount] = []
        # code: số trang bỏ mềm theo page_role, chỉ xét trang dropped = true; tổng count = pages_dropped. Chỉ có vai trò
        # với count ≥ 1; sắp theo thứ tự khai báo của PageRole (ổn định)
    proposal_job: ProposalJob | None = None
        # code: job đề xuất cặp MỚI NHẤT của tài liệu (§5.8) trong ProposalJobRuntime: job có document_id = meta.doc_id,
        # mọi trigger, created_at lớn nhất (hoà: tạo sau thắng, như GET …/jobs). Chép nguyên ProposalJob (status,
        # batches_done/batches_total, proposed_count, rejected_count, rejected_by_reason, …); không có job ⇒ null
    proposal_job_absent_reason: ProposalJobAbsentReason | None = None
        # code: lý do không có proposal_job; null ⇔ proposal_job khác null (xem quy tắc dưới khối mã)
    # --- thêm 2026-11-01 (ADR-010 mục 15; duyệt 2026-11-01 (ADR-010)). Lưu ở cột documents.layout_version,
    # documents.layout_settings_sha256 (migration 0007, §5.13)
    layout_version: int | None = Field(default=None, ge=1)
        # code: LAYOUT_VERSION (domain/blocks.py) của code nhập tài liệu; null CHỈ với tài liệu cũ (nhập trước khi có khối:
        # giữ build_chunks, không tự dựng lại, I4). Tài liệu mới luôn ghi giá trị này, kể cả khi mọi trang lớp chữ không dựng
        # được khối (các trang đó mang LAYOUT_FAILED) (đề xuất suy ra (orchestrator))
    layout_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
        # code: sha256 JSON chuẩn hoá của {layout_version, fingerprints} (ADR-010 mục 15); null ⇔ layout_version null

class ReviewReasonCount(StrictModel):            # thêm 2026-11-01 (T1e; đề xuất suy ra)
    reason: ReviewReason
    count: int = Field(ge=1)                      # code: = len(pages)
    pages: list[int]                              # code: page_number tăng dần, không trùng

class DroppedRoleCount(StrictModel):             # thêm 2026-11-01 (T1e; đề xuất suy ra)
    reason: Literal["cover_or_course_nav", "chapter_nav", "build_step"]   # các PageRole được phép bỏ mềm (validator ở trên)
    count: int = Field(ge=1)                      # code: = len(pages)
    pages: list[int]                              # code: page_number tăng dần, không trùng

ProposalJobAbsentReason = Literal[               # thêm 2026-11-01 (T1e; đề xuất suy ra, chờ xác nhận)
    "not_confirmed",     # tài liệu chưa `confirmed` (còn processing/review/failed): chưa cắt chunk nên chưa có job
    "past_exam",         # source_kind = past_exam: không cắt chunk (§6.5), không có job
    "no_chunks",         # tài liệu confirmed nhưng Vault không có chunk nào của tài liệu
    "llm_unavailable",   # khả năng concept_pair_llm_proposal không khả dụng (GET /v1/runtime, §5.12, §11.4)
    "not_retained",      # đủ điều kiện nhưng runtime không còn job nào của tài liệu: app khởi động lại, job bị đẩy khỏi
                         # 100 job giữ lại, tạo job lỗi lúc xác nhận, hoặc tài liệu xác nhận khi LLM chưa cấu hình rồi
                         # sau đó mới cấu hình. Người dùng chạy lại bằng POST /v1/vault/concept-pairs/jobs (§6.6)
]
```

- **Quy tắc `proposal_job` / `proposal_job_absent_reason` (T1e; đề xuất suy ra, chờ xác nhận).** Mọi endpoint trả `DocumentImport` (§6.5: `POST /v1/documents`, `GET /v1/documents/{document_id}`, `POST …/confirm`, `PATCH …/chapter`) điền ĐÚNG MỘT trong hai field. Có job của tài liệu trong runtime ⇒ `proposal_job` (kể cả khi LLM đã bị gỡ cấu hình sau đó). Không có ⇒ lý do đầu tiên đúng theo thứ tự: `not_confirmed` → `past_exam` → `no_chunks` → `llm_unavailable` → `not_retained`. Lý do tính lại ở mỗi lần đọc từ trạng thái hiện tại (không lưu). `llm_unavailable` lấy từ CÙNG hàm khả năng của `GET /v1/runtime` (§5.12), không phải nguồn thứ hai. Validator: hai field không cùng khác null; `proposal_job.document_id = meta.doc_id`.
- Tiến độ job: frontend hỏi lại `GET /v1/documents/{document_id}` (hoặc `GET /v1/vault/concept-pairs/jobs/{job_id}`, §6.6) 1–2 s một lần khi `proposal_job.status ∈ {queued, running}`; mọi con số do backend đếm (I1).
- `DocumentSummary` (§6.5) KHÔNG thêm các field T1e (đề xuất suy ra: màn kết quả đọc `DocumentImport`; danh sách tài liệu không cần).

```python
# thêm 2026-10-31 (GĐ1 1A, duyệt 2026-10-31; tên là đề xuất suy ra, chờ xác nhận)
ChapterFlag = Literal[
    "CHAPTER_NUMBER_NOT_IN_FILENAME",  # tên file không có số nguyên 1–2 chữ số ⇒ dùng chương trống nhỏ nhất
    "CHAPTER_NUMBER_SHARED",           # THÔNG TIN: số trong tên file trùng chương đã có của môn ⇒ GIỮ số đó (slide + giáo trình cùng chương hợp lệ)
]
# CHAPTER_NUMBER_TAKEN đã bỏ (người dùng duyệt 2026-11-01), thay bằng CHAPTER_NUMBER_SHARED
class ChapterSource(StrictModel):
    code: Literal["user", "file_name", "next_free"]
        # "user": người dùng nhập ở form hoặc PATCH …/chapter; "file_name": số trong tên file; "next_free": chương trống
        # nhỏ nhất. Tài liệu nhập trước 2026-10-31 (chapter_code bắt buộc ở form) ⇒ "user"
    title: Literal["user", "repeated_header", "file_name", "llm"] | None
        # null ⇔ chapter_title null (tài liệu nhập trước migration 0004)
```

**Chương tự động (GĐ1 1A; duyệt 2026-10-31).** `chapter_code` vẫn là khoá của chunk, `TheorySlotSpec` và `ExamBlueprint` (giữ mẫu `^ch[0-9]{1,2}(\.[0-9]{1,2})?$`); chỉ nguồn của nó đổi.
- **Số chương** khi form không có `chapter_code`: số nguyên 1–2 chữ số đầu tiên trong TÊN FILE (đã bỏ đuôi), tức đoạn chữ số ASCII liền nhau đầu tiên dài 1 hoặc 2 ký tự, không liền với chữ số khác (đoạn ≥ 3 chữ số như `2020` bị bỏ qua); `chapter_code = "ch" + str(int(số))` (`ch02` thành `ch2`). KHÔNG đọc từ trang 1 hay nội dung tài liệu. `chapter_source.code = "file_name"`. Cách tách đoạn số là đề xuất suy ra.
- **Số trùng chương đã có của môn** (một tài liệu khác cùng `subject_code` nhập qua §6.5 đã có `chapter_code` đó) ⇒ GIỮ số đó (`chapter_source.code = "file_name"`) và thêm cờ THÔNG TIN `CHAPTER_NUMBER_SHARED`: slide và giáo trình cùng chương là hợp lệ (người dùng duyệt 2026-11-01; `CHAPTER_NUMBER_TAKEN` đã bỏ, không còn ở contract).
- **Tên file KHÔNG có số** ⇒ `chapter_code = "chN"` với N là số nguyên ≥ 1 nhỏ nhất chưa tài liệu nào của môn dùng; `chapter_source.code = "next_free"`; thêm cờ `CHAPTER_NUMBER_NOT_IN_FILENAME`. Cờ chương (`CHAPTER_NUMBER_NOT_IN_FILENAME`, `CHAPTER_NUMBER_SHARED`) chỉ để hiển thị, không phải `ReviewReason`, KHÔNG chặn xác nhận. Người dùng tự nhập `chapter_code` ở form thì giữ hành vi cũ (được phép trùng chương của tài liệu khác, ví dụ slide và giáo trình cùng chương).
- **Tên chương `chapter_title`:** ứng viên theo thứ tự (1) dòng header/footer lặp của tài liệu (dòng `is_header_footer = true`, sau ghép ký tự rời của GĐ0; bỏ dòng mà chuỗi chuẩn hoá chỉ gồm `#`, dấu câu và khoảng trắng), xếp theo số trang có dòng đó giảm dần, rồi mép trên tăng dần, rồi chuỗi; (2) tên file đã làm sạch (bỏ đuôi, `_` và `-` thành dấu cách, gộp khoảng trắng, cắt 300 ký tự). Loại ứng viên trùng (NFC + `casefold` + gộp khoảng trắng) với một ứng viên header của tài liệu KHÁC cùng môn, với `chapter_title` của tài liệu khác cùng môn, hoặc với `display_name` hay `subject_code` của môn (§5.14). Lấy ứng viên header đầu tiên còn lại (`title = "repeated_header"`); không còn thì tên file đã làm sạch (`title = "file_name"`), kể cả khi trùng (để luôn có tên) (đề xuất suy ra). Lúc nhận file (202) `chapter_title` là tên file đã làm sạch; ứng viên header được xét ở bước kết thúc lần đọc, chỉ khi `chapter_source.title` chưa là `"user"`.
- **LLM (nếu có khả năng `concept_pair_llm_proposal`, §11.4):** chỉ được CHỌN một ứng viên hoặc LÀM GỌN một ứng viên, với kết quả là chuỗi con (sau NFC, gộp khoảng trắng) của đúng ứng viên đó, dài 1–300 ký tự; sai thì bỏ kết quả LLM, giữ lựa chọn tất định. Chỉ gửi danh sách ứng viên (chuỗi), KHÔNG gửi trang bìa hay văn bản trang. Chạy sau bước kết thúc lần đọc, ngoài khoá của tài liệu, không chặn trạng thái `review`; lỗi LLM chỉ ghi log (đề xuất suy ra). `title = "llm"`.
- **Đổi:** `PATCH /v1/documents/{document_id}/chapter` (§6.5). Đổi `chapter_code` chỉ khi tài liệu chưa `confirmed` và Vault chưa có chunk của tài liệu (sau đó 409); đổi `chapter_title` lúc nào cũng được. Giá trị người dùng đặt có nguồn `"user"`, xoá cờ chương, và không bị bước tự động ghi đè.
- **Nhập lại cùng nội dung** (cùng sha256, cùng `subject_code`) ⇒ trả bản ghi cũ như §6.5 bước 3, không tính lại chương hay tên.

**Tự xác nhận cấp trang (GĐ1 1B; ADR-008 mục 7, duyệt 2026-10-31).** Cấp KHỐI chờ đề xuất `docs/proposals/block-level-lock.md` (file chưa tồn tại, sẽ được viết; chưa duyệt): mục này KHÔNG có contract khoá cấp khối và KHÔNG đổi các lỗi 409 hiện có.
- **Luật độ tin cậy nguồn `source_trust_v1`** (một luật dùng chung cho `page_clean_v1` ở đây và cổng G1 của cặp khái niệm, §5.8; thêm 2026-10-31, đề xuất suy ra, chờ xác nhận): trang là nguồn đáng tin khi (a) `tier_used = "text_layer"` VÀ `scan_text_layer = false`, HOẶC (b) `tier_used = "ocr"` VÀ `cross_check.status = "ok"`. Mọi trường hợp khác không đáng tin: `scan_text_layer = true`, `tier_used = "vlm"`, `tier_used = "layout"`, trang OCR có `cross_check.status` khác `ok` (`unavailable`, `mismatch`, `not_applicable`), trang `pending` hay `failed`. Một hàm thuần duy nhất ở `domain/` hoặc `services/` cài luật này; hai nơi gọi không viết lại điều kiện.
- **Điều kiện** (luật `page_clean_v1`): `DocumentImport.auto_confirm = true`; tài liệu `source_kind` khác `past_exam` (trang đề đi tiếp vào Gate qua item; giữ người xác nhận: đề xuất suy ra, phương án an toàn); trang qua `source_trust_v1`; trang `status = "ok"` (tức `review_reasons` rỗng), `page_role = "content"`, `dropped = false`, chưa `confirmed`, `auto_confirm_undone = false`, có ít nhất một dòng có chữ (§5.7 "Chốt trạng thái"), vừa giới hạn trang của Vault (`_fits_vault`). Thêm 2026-11-01 (ADR-010 mục 13; đề xuất suy ra (orchestrator), chặt hơn, không nới I5): trang có khối mang một trong các cờ `layout_uncertain`, `table_unresolved`, `table_token_mismatch`, `vlm_gate_rejected`, `crop_pending`, `crop_failed`, `crop_capped`, `ai_proposal_rejected` (§5.15) KHÔNG được tự xác nhận (bước ảnh cắt chạy TRƯỚC tự xác nhận ở bước kết thúc lần đọc, §5.15, nên `crop_pending` lúc đó nghĩa là ảnh chưa cắt được) (trang chưa có khối: điều kiện này đúng hiển nhiên, nên hành vi hiện có của `page_clean_v1` không đổi; tên luật giữ nguyên). "Không BLOCKING": trang chưa có `GateIssue`; điều kiện này áp khi có khối (sau khi đề xuất cấp khối được viết và duyệt).
- **Khi nào:** ở bước kết thúc lần đọc, SAU bước 0D (vai trò trang), dưới khoá của tài liệu. Ghi `confirmed = true`, `confirmed_at`, `confirmed_by = "auto"`, `confirm_rule = "page_clean_v1"`, `version` + 1 (so version như xác nhận thường).
- **Tự xác nhận KHÔNG đóng tài liệu và KHÔNG cắt chunk** (sửa 2026-10-31 theo review; đề xuất suy ra, chờ xác nhận, khớp mục tiêu "bấm MỘT lần Chấp nhận"). Tự xác nhận chỉ đặt trạng thái trang. Tài liệu có ít nhất một trang `confirmed_by = "auto"` chỉ chuyển `confirmed` (và cắt chunk, tạo job đề xuất cặp) khi NGƯỜI DÙNG gọi `POST /v1/documents/{document_id}/confirm` (nút "Chấp nhận", §6.5); bước chốt chạy ở cuối lần đọc hay sau khi người dùng xác nhận từng trang KHÔNG đóng tài liệu đó (quy tắc ở "Chốt trạng thái" dưới đây). Nhờ vậy, tới lúc người dùng bấm "Chấp nhận", văn bản tài liệu còn mở: hoàn tác bỏ mềm (`…/restore`), hoàn tác tự xác nhận (`…/unconfirm`), đổi `chapter_code` (`PATCH …/chapter`) và luật đánh giá lại trang bìa của tài liệu nhập trước (0D) đều còn chạy được.
- **Audit:** `confirmed_by`, `confirm_rule`, `confirmed_at` trong `PageExtraction` là bản ghi audit của lần tự xác nhận (actor `"auto"`); không thêm bảng, không ghi `PageEditAudit` (không đổi dòng nào) (đề xuất suy ra). Khi người dùng bấm "Chấp nhận", trang tự xác nhận giữ `confirmed_by = "auto"` (audit cho biết máy xác nhận trang, người chấp nhận tài liệu).
- **Hoàn tác một chạm:** `POST …/pages/{page_number}/unconfirm` (§6.5), cho trang `confirmed_by = "auto"` khi văn bản tài liệu còn mở, tức tới trước lúc người dùng bấm "Chấp nhận". Sau đó (tài liệu `confirmed`, chunk đã cắt) hoàn tác trả 409; gỡ chunk đã cắt không thuộc GĐ1.
- **I5:** trang còn bất kỳ `review_reasons` nào, trang bỏ mềm, trang có vai trò khác `content`, hay trang không qua `source_trust_v1` KHÔNG BAO GIỜ được tự xác nhận; chúng vẫn qua màn hình xem lại như trước. Không văn bản nào vào Vault khi người dùng chưa bấm xác nhận ít nhất một lần cho tài liệu đó.
- **Test đề xuất cho bảng CLAUDE.md §9 (GĐ1; đề xuất, chờ người dùng duyệt bảng §9):** dòng I5: `test_auto_confirm_never_confirms_flagged_page` (trang có `review_reasons`, bỏ mềm, `page_role` khác `content`, không qua `source_trust_v1`, hay tài liệu `past_exam` không bao giờ được tự xác nhận), `test_auto_confirm_never_closes_document` (tự xác nhận và bước chốt cuối lần đọc không đóng tài liệu, không cắt chunk; chỉ `POST /v1/documents/{document_id}/confirm` đóng), `test_unconfirm_restores_review_and_blocks_reauto`; dòng I3: `test_pointer_cut_is_verbatim_substring`, `test_pointer_ambiguous_anchor_rejected_after_one_retry`, `test_term_exact_must_be_in_span`, `test_term_span_cross_chunk_rejected` (§5.8 giao thức con trỏ).

**Chốt trạng thái tài liệu (duyệt 2026-10-26 chiều).** Bước chốt trạng thái (settle) chạy sau mỗi lần xác nhận trang hoặc xác nhận cả tài liệu (§6.5), và chạy cả ở đường `failed` (khi `status` vừa thành `failed` vì worker không mở lại được tài liệu), không chỉ ở đường đọc xong bình thường (`review`):
- Mọi trang có văn bản đã xác nhận ⇒ `status = "confirmed"`, kể cả khi `status` trước đó là `failed`; tài liệu khác `past_exam` khi đó được cắt chunk vào Vault (§6.5 "Xác nhận", §12.1).
- Thêm 2026-10-31 (GĐ1 1B; đề xuất suy ra, chờ xác nhận): tài liệu có ít nhất một trang `confirmed_by = "auto"` chỉ được chốt thành `confirmed` ở bước chốt chạy trong `POST /v1/documents/{document_id}/confirm` (người dùng bấm "Chấp nhận"); bước chốt ở cuối lần đọc, ở đường `failed`, và sau `POST …/pages/{page_number}/confirm` không đóng tài liệu đó (tài liệu giữ `review` hoặc `failed`). Tài liệu không có trang tự xác nhận (ví dụ `auto_confirm = false`) giữ quy tắc cũ.
- Trang "có văn bản" là trang không `failed` và có ít nhất một dòng có chữ (dòng có chữ = `text` sau khi bỏ khoảng trắng đầu/cuối khác rỗng; dòng chỉ gồm khoảng trắng coi như không có văn bản; theo code `_has_text`, PR #83) (suy ra từ điều kiện 409 "trang không có dòng nào có văn bản" của `POST …/confirm`, §6.5); trang `failed` không có văn bản để xác nhận nên không chặn việc chốt.
- Thêm 2026-10-31 (GĐ0; đề xuất suy ra, chờ duyệt): dòng `is_header_footer = true` không phải dòng có chữ và không vào văn bản xác nhận hay chunk (§12.1); trang `dropped = true` không tính là trang có văn bản nên không chặn việc chốt, và không bao giờ vào văn bản xác nhận hay chunk khi chưa hoàn tác rồi xác nhận (I5, ADR-008 mục 7).
- Đề xuất (chi tiết), chờ duyệt: tài liệu không có trang nào có văn bản (ví dụ mọi trang `failed`) KHÔNG chuyển sang `confirmed`, vì không có gì để cắt chunk; nó giữ `review` hoặc `failed`, người dùng xoá rồi nhập lại.

**Tài liệu nhập trước khi có `chapter_code` (duyệt 2026-10-26 chiều).** Bản ghi nhập qua §6.5 trước khi có field form `chapter_code` có `documents.chapter_code = null` trong kho, nên không dựng được `DocumentImport` (field bắt buộc). Với các bản ghi này, `GET /v1/documents/{document_id}`, `POST /v1/documents` cùng nội dung, `POST …/pages/{page_number}/confirm` và `POST /v1/documents/{document_id}/confirm` trả 409 (§6.5). `DELETE /v1/documents/{document_id}` vẫn chạy để người dùng xoá rồi nhập lại. `detail` (đề xuất (chi tiết), chờ duyệt): chuỗi cố định "Tài liệu được nhập trước khi có chapter_code; hãy xoá tài liệu rồi nhập lại".

**Khử nhiễu trang và vai trò trang (GĐ0; người dùng duyệt 2026-10-31; ADR-008 mục 6 "Khử nhiễu trang và vai trò trang", mục 9, 12).** Thay các câu 2026-10-30 "trang giữ lớp chữ có `has_figure` mang `TIER3_UNAVAILABLE`" và "nhánh đường vẽ của `has_figure` tính mọi ảnh" (đã thay 2026-10-31). Chỗ ghi "suy ra" là thiết kế của người viết spec để code không phải đoán, chờ người dùng xác nhận.

*Nguyên tắc chung*
- **Không viết lại chữ (I3).** Lọc chỉ được (i) bỏ trọn các ký tự của một đối tượng chữ bị loại vì chữ ẩn, TRƯỚC khi dựng dòng, và (ii) bỏ ký tự trắng hay ngắt dòng do PDFium tự sinh (`FPDFText_IsGenerated`) giữa các ký tự rời được ghép. Không thêm, đổi hay sắp lại ký tự nào của lớp chữ: mọi `ExtractedLine.text` còn lại vẫn là chuỗi ký tự của lớp chữ theo thứ tự đọc (NFC). Đánh dấu (header/footer, cờ dòng, cờ trang, vai trò trang) không đổi `text`. Không tự sửa lỗi font, không vẽ lại hình, không tóm tắt.
- **Không chuỗi theo môn hay ngôn ngữ trong code** (CLAUDE.md §1): luật GĐ0 chỉ dùng hình học, màu, hash và tập token; không có mẫu như "Chương", "ARM". Nếu sau này cần mẫu ngôn ngữ thì chỉ nằm trong tệp cấu hình theo ngôn ngữ (vi/en).
- **Vùng ngưỡng ở giữa không bỏ gì, chỉ gắn cờ** (chữ ẩn 1.25–2.0, dHash 5–8 bit).
- **Nơi chạy:** 0A, 0B, 0C chạy trong worker đọc PDF (ADR-008 mục 3; tài liệu không tin cậy chỉ mở trong worker) bằng hàm thuần ở `infrastructure/document_readers/`, dùng chung với tiến trình cha như `ocr_reason` hiện nay; dữ liệu cấp tài liệu (template, header/footer) lấy từ lượt quét trước trang đầu (ADR-008 mục 3). 0D chạy ở service (`services/document_import`), không ở worker (đề xuất suy ra, chờ duyệt; lý do: 0D cần mọi trang đã đọc và trang của tài liệu khác trong kho, worker không được truy cập kho).
- 0A–0C áp cho trang PDF số đọc lớp chữ (kể cả bước quyết định OCR); trang OCR giữ nguyên luật tầng 2, chỉ thêm `own_images`. Dữ liệu thô mới của worker (`content_path_area_ratio`, đối tượng theo thứ tự vẽ, khoá PATH/TEXT) là giao thức nội bộ của `document_readers/protocol.py`, không phải contract API.

*0A Đối tượng PDF, template, nền, cờ hình*
- **Duyệt đối tượng:** worker duyệt đệ quy `FPDF_PAGEOBJ_FORM` tới độ sâu `PDF_FORM_MAX_DEPTH`, ma trận của đối tượng = ma trận form nhân ma trận cha; hộp của IMAGE, PATH, TEXT là hộp sau biến đổi, chuẩn hoá về [0, 1] theo khổ trang đã xoay (cùng cách `normalize_box`), cắt theo trang. Thứ tự vẽ (z-order) là chỉ số theo thứ tự duyệt sâu (nội dung form ở đúng vị trí của form). Trả nợ đo form XObject (ADR-004 BE-008): ch3 tr.25–27, 80–82 phải đo khoảng 0.4–0.5, không phải 0.12 (ADR-008 "Kết quả đo GĐ0").
- **Trần:** trang có quá `PAGE_OBJECTS_MAX` đối tượng sau khi duyệt (hoặc form sâu quá `PDF_FORM_MAX_DEPTH`) thì không lọc chữ ẩn, không xét `TEXT_OCCLUDED`, không nhận nền PATH, mọi ảnh và đường vẽ đã duyệt tính là nội dung, và trang giữ lớp chữ có `has_figure` mang `DENOISE_UNAVAILABLE` (đề xuất suy ra, chờ duyệt).
- **Template (một hàm `find_template_images` cho ba loại):** chữ ký lặp ở cùng chỗ (hộp lượng tử theo `TEMPLATE_IMAGE_BBOX_STEP`, dung sai ±1 ô đối xứng, không bắc cầu, như ảnh template ở trên) trên ≥ `TEMPLATE_IMAGE_MIN_PAGES` trang:
  - IMAGE: khoá `ImageKey` như trên; ngưỡng tỉ lệ `TEMPLATE_MIN_PAGE_RATIO`.
  - PATH: (hộp lượng tử, màu tô RGBA hoặc null khi không tô, màu viền RGBA hoặc null khi không viền, số đoạn của path); ngưỡng tỉ lệ `TEMPLATE_MIN_PAGE_RATIO`. Trang có quá `TEMPLATE_SCAN_MAX_PATHS_PER_PAGE` PATH: PATH của trang đó không có khoá (không bao giờ là template), lượt quét vẫn tiếp tục (đề xuất suy ra).
  - TEXT: (hộp lượng tử của dòng, chuỗi chuẩn hoá: NFC, mọi chữ số Unicode `Nd` thành `#`, gộp chuỗi khoảng trắng thành một dấu cách, bỏ khoảng trắng đầu/cuối; dòng rỗng sau chuẩn hoá không có khoá). Dòng của lượt quét dựng bằng CÙNG hàm dựng dòng của lúc đọc trang (kể cả ghép ký tự rời) nhưng không lọc chữ ẩn (lượt quét không render). Ngưỡng của TEXT là của header/footer (0B(5)): ≥ `HEADER_FOOTER_MIN_PAGE_RATIO` số trang VÀ `(p_max − p_min) / N ≥ HEADER_FOOTER_MIN_SPREAD`, với `p_max`, `p_min` là số trang lớn nhất, nhỏ nhất có khoá và `N = page_count` (dùng ngưỡng riêng cho TEXT trong cùng hàm: đề xuất suy ra, chờ duyệt).
  - `MAX_TEMPLATE_KEYS` áp riêng cho mỗi loại. Lượt quét vượt trần, quá hạn hoặc lỗi ⇒ không có template loại nào và không có header/footer (hành vi an toàn, kèm `DENOISE_UNAVAILABLE` ở 0C).
- **Nền:** CHỈ gồm (i) PATH tô đặc (có chế độ tô, alpha màu tô = 255) phủ ≥ `BACKGROUND_PATH_MIN_AREA` diện tích trang VÀ vẽ trước (z-order nhỏ hơn) mọi đối tượng chữ có hộp giao với nó, HOẶC (ii) ảnh đã là template. Ảnh KHÔNG phải template thì KHÔNG BAO GIỜ là nền. Test bắt buộc: ch4 tr.22 (ảnh nội dung phủ 0.98 vẽ dưới header) vẫn OCR theo điều kiện (d) `image_covers_page`; cùng test trên PDF tổng hợp tương đương (ảnh không-template phủ ≥ 0.98 vẽ trước một dòng header).
- **`has_figure` trên PDF số** (thay luật 2026-10-30; trả ADR-004 BE-007): chỉ tính đối tượng không-template, không-nền. Bật khi độ phủ ảnh nội dung (diện tích hợp của ảnh không-template) `≥ FIGURE_IMAGE_AREA_MIN`, hoặc khi trang có ít nhất một ảnh nội dung VÀ `content_path_area_ratio` (tổng diện tích chặn ở 1 của PATH không-template, không-nền) `≥ FIGURE_PATH_AREA_MIN` (giữ điều kiện "có ảnh" để khung viền quanh code không bật cờ một mình: đề xuất suy ra). `own_images` là các ảnh nội dung của trang.

*0B Chất lượng chữ (chỉ dòng engine `text_layer`)*
1. **Chữ ẩn** (đơn vị: đối tượng chữ, kể cả trong form). CHỈ áp cho chữ được vẽ (render mode ≠ 3). Màu chữ là màu tô, hoặc màu viền khi đối tượng chỉ viền. Nền thật là màu trung vị các điểm ảnh trong hộp đối tượng chữ trên ảnh render `HIDDEN_TEXT_RENDER_DPI` của trang khi không vẽ chữ (worker ẩn đối tượng chữ trong bộ nhớ trước khi render, không ghi file; cách ẩn là đề xuất suy ra). Tương phản = `(L_sáng + 0.05) / (L_tối + 0.05)`, `L` là độ chói tương đối sRGB (công thức WCAG 2.x; đề xuất suy ra). Tương phản `≤ HIDDEN_TEXT_DROP_MAX` ⇒ ký tự của đối tượng bị loại trước khi dựng dòng, cộng vào `hidden_chars_dropped`; `≥ HIDDEN_TEXT_KEEP_MIN` ⇒ giữ; ở giữa ⇒ giữ, dòng chứa ký tự của đối tượng mang cờ `LOW_TEXT_CONTRAST`. Chữ trắng trên nền màu (ch3 tr.13, 52, 61, 86) phải được GIỮ.
   - **Lớp OCR của PDF scan:** trang có tỉ lệ ký tự render mode 3 `> SCAN_TEXT_LAYER_MODE3_MIN` VÀ `image_area_ratio` (mọi ảnh) `≥ SCAN_TEXT_LAYER_IMAGE_MIN` ⇒ `scan_text_layer = true`: giữ nguyên, không loại ký tự nào, không xét `TEXT_OCCLUDED` (cổng glyph và header/footer vẫn áp vì không bỏ chữ). Test phủ định bắt buộc: PDF tổng hợp dạng scan (ảnh phủ cả trang) có lớp chữ mode 3 không mất ký tự nào. Chữ mode 3 ở trang khác cũng không bị loại và không xét tương phản (đề xuất suy ra).
2. **Chữ bị che:** đối tượng chữ (mode ≠ 3, trang không `scan_text_layer`) mà hợp các hộp của ảnh (mọi ảnh) và PATH tô đặc có z-order lớn hơn nó che `≥ OCCLUSION_MIN_COVER` diện tích hộp của nó ⇒ dòng chứa ký tự của nó mang cờ `TEXT_OCCLUDED`. Chữ được giữ.
3. **Cổng glyph:** dòng có ký tự U+FFFD, ký tự điều khiển (`Cc`, trừ tab), ký tự vùng PUA U+E000–U+F8FF, hoặc ký tự U+00A1–U+00BF, U+00D7 (`×`), U+00F7 (`÷`) mà ký tự liền trước hoặc liền sau là chữ cái (`L*`) ⇒ cờ `FONT_ENCODING_SUSPECT`. Không sửa chữ. Giới hạn đã biết: ký hiệu đơn vị dính chữ (`25°C`, `µs`, `a×b`) cũng bị gắn cờ; trên ba chương đo, cổng bắt 1/1 lỗi (`¼q`, ch2 tr.42), 0 báo nhầm.
4. **Ghép ký tự rời** TRƯỚC khi dựng dòng: chuỗi ≥ `CHAR_MERGE_MIN_CHARS` ký tự liên tiếp theo thứ tự đọc, mỗi ký tự đứng riêng (bị PDFium tách bằng ký tự trắng hay ngắt dòng tự sinh), cùng baseline (mép dưới lệch ≤ `CHAR_MERGE_BASELINE_TOL` × chiều cao ký tự), khoảng cách giữa hai ký tự kề nhau lệch khỏi trung vị khoảng cách của chuỗi ≤ `CHAR_MERGE_GAP_TOL` × chiều cao ký tự ⇒ ghép thành một dòng, bỏ ký tự tự sinh giữa chúng; chỗ khoảng cách lớn hơn trung vị cộng dung sai là ranh giới từ, giữ một ký tự trắng tự sinh. Chi tiết luật là đề xuất suy ra (yêu cầu gốc: "cùng baseline, khoảng cách đều"); test: header chương bị tách thành từng chữ (ch4) thành một dòng, dòng thường không đổi.
5. **Header/footer:** dòng khớp một khoá TEXT template (0A; cùng chuỗi chuẩn hoá, hộp trong ±1 ô) ⇒ `is_header_footer = true`. Dòng vẫn nằm trong `lines` để tương thích nhưng không vào văn bản xác nhận hay chunk, không đếm vào token của 0C, 0D, và không đếm vào `non_ws` của điều kiện (c).

*0C Khi nào phải đọc ảnh (thay luật 2026-10-30)*
- **Token** (dùng chung 0C, 0D): các chuỗi con tối đa khớp `\w+` (Unicode) của văn bản đã NFC và `casefold` (đề xuất suy ra). `content_token_count` đếm trên các dòng lớp chữ sau lọc chữ ẩn, trừ dòng header/footer.
- **Luật chọn tầng** (ADR-008 mục 6; thứ tự xét): (a) rỗng; (b) rác; (c) thưa: `non_ws` (sau lọc chữ ẩn, trừ dòng header/footer; trả BE-009) `< TEXT_SPARSE_CHARS` VÀ độ phủ ảnh nội dung `≥ TEXT_SPARSE_IMAGE_MIN`; (d) `image_covers_page` như cũ (`non_ws` của mọi dòng sau lọc chữ ẩn); (e) mới, `image_needs_reading`: `content_token_count < FIGURE_TEXT_MIN_TOKENS` VÀ độ phủ ảnh nội dung (không-template) `≥ IMAGE_READ_AREA_MIN` ⇒ OCR cả trang ở tầng 2 hiện có (OCR riêng vùng ảnh và VLM là việc sau; đề xuất suy ra). Trang thoả điều kiện của (e), dù được đưa đi OCR bởi luật nào, mang `IMAGE_NEEDS_READING` ("vùng ảnh cần đọc + cờ"). Thiếu engine OCR ⇒ `failed` `OCR_ENGINE_MISSING` như mọi trang OCR. Phải bắt được ch3 tr.24–27, 42–43, 51, 62–66, 73, 80–82, 85 và ch4 tr.22.
- **Trang giữ lớp chữ có `has_figure`:** `content_token_count ≥ FIGURE_TEXT_MIN_TOKENS` ⇒ hình chỉ được ghi vùng (`own_images`) để sau này CẮT PNG gắn vào khái niệm; không OCR, không VLM, không lý do xem lại nào vì hình. Việc cắt PNG và gắn vào khái niệm thuộc GĐ1 (đề xuất suy ra). Trường hợp còn lại (`< FIGURE_TEXT_MIN_TOKENS` token nhưng ảnh nội dung `< IMAGE_READ_AREA_MIN`, ví dụ hình chỉ là đường vẽ) xử lý như `≥ FIGURE_TEXT_MIN_TOKENS` (đề xuất suy ra). Hình trên PDF số không còn sinh `TIER3_UNAVAILABLE`; `TIER3_UNAVAILABLE` chỉ còn cho bảng và công thức.
- **Lượt quét không xong:** lượt quét cấp tài liệu có trạng thái khác `ok` (`over_cap`, `timeout`, `error`), hoặc trang vượt trần đối tượng (0A) ⇒ trang giữ lớp chữ có `has_figure` mang `DENOISE_UNAVAILABLE` thay cho việc được miễn ở trên (vì header lặp chưa được trừ, số token có thể bị thổi lên) (đề xuất suy ra, chờ duyệt). Tài liệu 1–2 trang (lượt quét `ok`, không có template) không thuộc trường hợp này.

*0D Vai trò trang và bỏ mềm*
- **Khi nào:** ở bước kết thúc lần đọc (`_finish` của `services/document_import`), dưới khoá của tài liệu, TRƯỚC khi ghi trạng thái cuối và trước bước chốt (settle). Tính lại cho cả tài liệu mỗi lần kết thúc lần đọc (kể cả lần xếp lại khi khởi động), bằng hàm thuần trên các `PageExtraction` đã lưu. Không đổi: trang `pending`, `failed` (giữ `content`), trang đã `confirmed`, trang `restored`. Tài liệu đã `confirmed`, hoặc Vault đã có chunk của tài liệu ⇒ không bỏ mềm trang nào. Ghi trang đổi qua `update_page` có so version (version + 1); lệch version ⇒ đọc lại và thử lại tối đa `SETTLE_ATTEMPTS` lần, hết lượt thì ghi cảnh báo và trang giữ như cũ (không bỏ mềm là phía an toàn) (đề xuất suy ra).
- **Tập token:** `R(p)` = tập token của các dòng không phải header/footer của trang `p` (mọi engine); `T(p)` = `R(p)` trừ các token có trên ≥ `PAGE_ROLE_COMMON_TOKEN_RATIO` × `page_count` trang của tài liệu. Jaccard = `|A ∩ B| / |A ∪ B|`. Trang có `|T(p)| < PAGE_ROLE_MIN_TOKENS` không tham gia `chapter_nav`, `build_step`; trang có `|R(p)| < PAGE_ROLE_MIN_TOKENS` không tham gia `cover_or_course_nav` (đề xuất suy ra).
- **Luật chặn theo ảnh:** ảnh `a` của trang `p` "xuất hiện" ở trang `q` khi `q` có ảnh `b` cùng `sha256` (khác null), hoặc khoảng cách Hamming giữa `dhash` ≤ `BUILD_STEP_DHASH_SAME_MAX`. Trang có ảnh riêng (`own_images`) không xuất hiện ở trang so sánh KHÔNG BAO GIỜ là `cover_or_course_nav`, `chapter_nav` hay `build_step`.
- **Thứ tự xét:** `cover_or_course_nav`, rồi `chapter_nav`, rồi `build_step`/`parallel_visual_series`; trang đã có vai trò ở bước trước không xét tiếp.
- **`cover_or_course_nav`:** tồn tại trang `q` của tài liệu KHÁC cùng `subject_code` (nhập qua §6.5, còn trong kho, `q` không `pending`/`failed`) với `Jaccard(R(p), R(q)) ≥ COVER_JACCARD_MIN` và luật chặn theo ảnh không chặn ⇒ `page_role = cover_or_course_nav`, bỏ mềm, `related_pages` = các trang `q` đạt (sắp theo `document_id`, `page_number`). **Thứ tự nhập:** tài liệu nhập trước chưa có tài liệu khác để so. Khi tài liệu sau đọc xong mà trang `p` của nó khớp trang `q` của tài liệu trước `D'`, service đánh giá lại `q` theo cùng luật (chiều `q` so với `p`): nếu `D'` chưa `confirmed`, Vault chưa có chunk của `D'`, `q` chưa `restored`, và `q` chưa được NGƯỜI DÙNG xác nhận (`confirmed = false`, hoặc `confirmed_by = "auto"`: khi đó bỏ luôn tự xác nhận của `q`, tức `confirmed = false`, `confirmed_by = null`, `confirm_rule = null`) thì `q` cũng bị bỏ mềm với `related_pages = [p]`. Bước này chạy SAU khi nhả khoá của tài liệu đang đọc, lấy khoá từng `D'` một (không giữ hai khoá cùng lúc). Vì tự xác nhận không đóng tài liệu (§5.7 "Tự xác nhận cấp trang"), `D'` còn mở tới khi người dùng bấm "Chấp nhận", nên luật đi ngược chạy được cho mọi tài liệu chưa được người dùng chấp nhận. **Giới hạn còn lại:** `D'` đã được người dùng chấp nhận (`confirmed`) hoặc đã có chunk ⇒ không đổi gì, không xoá chunk; trang bìa của tài liệu đó vẫn ở Vault. Phép đo GĐ0 phải báo nav recall cho CẢ HAI trường hợp: tài liệu trước còn mở, và tài liệu trước đã được chấp nhận (đề xuất suy ra, chờ duyệt). Tài liệu bị xoá sau đó không làm trang đã bỏ mềm tự hoàn tác; người dùng hoàn tác nếu cần.
- **`chapter_nav`:** ứng viên là trang tham gia có mọi dòng (không phải header/footer) dài ≤ `CHAPTER_NAV_MAX_LINE_CHARS` ký tự, `has_table = false`, `own_images` rỗng. Gom cụm theo `page_number` tăng dần: trang vào cụm đầu tiên mà `Jaccard(T)` với MỌI thành viên ≥ `CHAPTER_NAV_JACCARD_MIN` (không bắc cầu), không có thì mở cụm mới. Cụm có ≥ `CHAPTER_NAV_MIN_PAGES` thành viên VÀ trung vị khoảng cách `page_number` giữa hai thành viên liên tiếp ≥ `CHAPTER_NAV_MIN_MEDIAN_GAP` (thành viên rải rác) ⇒ mọi thành viên là `chapter_nav`, bỏ mềm, `related_pages` = các thành viên khác.
- **`build_step` / `parallel_visual_series`:** xét từng cặp kề `(i, i+1)` cùng tài liệu, cả hai tham gia và chưa có vai trò. Điều kiện chữ: (1) `|T(i) ∩ T(i+1)| / |T(i)| ≥ BUILD_STEP_SUBSET_MIN`; (2) mọi dòng có chữ (không phải header/footer) của `i` có một dòng ở `i+1` cùng `text` (sau bỏ khoảng trắng đầu/cuối) và mọi toạ độ `bbox` lệch ≤ `BUILD_STEP_POSITION_TOL` ("khối" ở GĐ0 là dòng; dòng `bbox` null làm (2) sai) (đề xuất suy ra). Khoảng cách ảnh `D`: với mỗi ảnh riêng `a` của `i`, `d(a) = 0` nếu `i+1` có ảnh cùng `sha256`, ngược lại là khoảng cách dHash nhỏ nhất tới ảnh của `i+1` (không có ảnh hay `dhash` null ⇒ không xác định); `D = max d(a)`, `i` không có ảnh riêng ⇒ `D = 0`. Khi (1) và (2) đúng:
  - `D ≤ BUILD_STEP_DHASH_SAME_MAX` ⇒ `i` là `build_step`, bỏ mềm, `related_pages = [i+1]`, `dhash_distance = D`. Chuỗi bước `i, i+1, i+2` ⇒ bỏ `i`, `i+1`, giữ bước cuối.
  - `D ≥ PARALLEL_SERIES_DHASH_MIN` (dHash > 8) ⇒ `i` và `i+1` là `parallel_visual_series`, GIỮ cả hai (ví dụ ch3 tr.62–66: chữ lặp nhưng mỗi trang một ảnh riêng).
  - Ở giữa ⇒ `i` GIỮ `content`, thêm `PAGE_ROLE_UNCERTAIN`, `role_evidence.rule = "build_step_uncertain"`, `related_pages = [i+1]`.
  - `D` không xác định ⇒ giữ `content`, không cờ (luật chặn theo ảnh).
- **`exercise`:** chỉ LLM gắn. GĐ0 không sinh giá trị này và không bỏ trang vì nó. GĐ1 (duyệt 2026-10-31) gắn nhãn bài tập ở CẤP CHUNK (`KnowledgeChunk.is_exercise`, §5.8, job đề xuất cặp §6.6); `page_role = "exercise"` vẫn chưa được sinh ở GĐ1 (đề xuất suy ra, chờ xác nhận). Luật nhận trang bài tập theo dấu "?" không dùng (đúng 2/16, nhầm 7).
- **Bỏ mềm:** `dropped = true`, `page_role` và `role_evidence` như trên. Trang bỏ mềm: không vào văn bản xác nhận, chunk hay Vault; không tính vào `pages_needs_review`; không chặn bước chốt tài liệu; không xác nhận, sửa dòng hay tạo item được cho tới khi hoàn tác (§6.5). Màn hình xem lại vẫn hiện trang bỏ mềm kèm lý do từ `role_evidence` (luật, trang liên quan); frontend chỉ hiển thị field, không tự suy luật. Hoàn tác một chạm: `POST …/pages/{page_number}/restore` (§6.5).
- **Lưu trữ:** mọi field mới nằm trong `PageExtraction` (cột `pages.extraction_json`, §5.13); không cần migration.

*Bất biến*
- **I3:** dòng còn lại là nguyên văn lớp chữ (nguyên tắc chung ở trên); trích dẫn vẫn kiểm trên văn bản đã xác nhận. Test bắt buộc (đề xuất): mọi `ExtractedLine.text` tầng 1 sau GĐ0 là chuỗi con theo thứ tự của văn bản lớp chữ thô của trang trên các PDF tổng hợp có chữ ẩn, ký tự rời, header/footer.
- **I5:** trang hoặc dòng còn cờ thì trang `needs_review` (validator), không xác nhận theo lô được; trang bỏ mềm không bao giờ vào Vault hay chunk khi chưa hoàn tác rồi xác nhận; số đếm tóm tắt (lấy, bỏ, cần xem) do backend đếm (`DocumentImport`, `DocumentSummary`), frontend chỉ hiển thị (I1).
- **Test đề xuất cho bảng CLAUDE.md §9 (đề xuất, chờ người dùng duyệt bảng §9):** dòng I3: `test_denoise_never_rewrites_text`; dòng I5: `test_content_image_never_background`, `test_scan_ocr_layer_keeps_all_text`, `test_dropped_page_never_chunked`, `test_own_image_page_never_dropped`, `test_dhash_mid_zone_flags_not_drops`, `test_denoise_unavailable_flags_figure_pages` (file gợi ý `tests/test_pdf_denoise.py`, `tests/test_page_roles.py`).
- **I4:** tài liệu đã nhập giữ văn bản, lý do xem lại và vai trò đã lưu (trang lưu trước GĐ0 đọc ra với `page_role = "content"`, không bỏ mềm); luật GĐ0 chỉ áp khi nhập, nên chỉ xoá rồi nhập lại mới đổi kết quả. Không có bước tự đọc lại. 0A–0C: cùng file và cùng cấu hình cho cùng kết quả (hàm thuần, không ngẫu nhiên). 0D cũng tất định trên cùng dữ liệu đầu vào, nhưng luật `cover_or_course_nav` so với các tài liệu khác của môn đang có trong kho, nên phụ thuộc thứ tự nhập và trạng thái chấp nhận của tài liệu trước: cùng file nhập ở thời điểm khác có thể nhận vai trò khác. Câu hỏi và cặp khái niệm đã sinh từ chunk cũ không đổi.

*Ngưỡng GĐ0* (field có giá trị mặc định của `PdfReaderConfig` ở `infrastructure/document_readers/config.py`, không phải biến môi trường, ADR-008 mục 12; tên trong code viết thường; service đọc qua `ImportRuntime.reader_config`):

| Tên | Mặc định | Ý nghĩa (so sánh) | Nguồn |
|---|---|---|---|
| `TEMPLATE_MIN_PAGE_RATIO` | 0.40 | Tỉ lệ trang tối thiểu của template IMAGE, PATH (`≥`, `(0, 1]`); đổi tên từ `TEMPLATE_IMAGE_MIN_PAGE_RATIO`. Giá trị chọn bằng phép đo GĐ0 (0.40 hay 0.60), số đo ở ADR-008 "Kết quả đo" | Người dùng 2026-10-31 (a); giá trị 0.40 người dùng duyệt 2026-11-01 |
| `TEMPLATE_IMAGE_MIN_PAGES` | 3 | Số trang tối thiểu của template mọi loại (`≥`, `≥ 3`); giữ tên | Người dùng 2026-10-31 (a) |
| `TEMPLATE_IMAGE_BBOX_STEP` | 0.01 | Bước lượng tử hộp cho mọi loại (1% trang); giữ tên | Duyệt 2026-10-30; yêu cầu GĐ0 0A |
| `TEMPLATE_SCAN_MAX_PATHS_PER_PAGE` | 4096 | Trang có nhiều PATH hơn: PATH của trang không có khoá | Đề xuất suy ra |
| `PDF_FORM_MAX_DEPTH` | 16 | Độ sâu tối đa khi duyệt form XObject | Đề xuất suy ra |
| `PAGE_OBJECTS_MAX` | 20000 | Số đối tượng tối đa mỗi trang sau khi duyệt (`>` là vượt trần, 0A) | Đề xuất suy ra |
| `BACKGROUND_PATH_MIN_AREA` | 0.50 | PATH tô đặc phủ `≥` mức này và vẽ trước chữ là nền | Người dùng 2026-10-31 (b) |
| `FIGURE_IMAGE_AREA_MIN` | 0.10 | Như cũ, nay trên ảnh nội dung (`≥`) | Duyệt 2026-10-30 |
| `FIGURE_PATH_AREA_MIN` | 0.25 | Như cũ, nay trên PATH nội dung (`≥`) | Duyệt 2026-10-30 |
| `HIDDEN_TEXT_RENDER_DPI` | 72 | DPI ảnh render để đo tương phản và dHash | Yêu cầu GĐ0 0B |
| `HIDDEN_TEXT_DROP_MAX` | 1.25 | Tương phản `≤` ⇒ loại chữ | Yêu cầu GĐ0 0B |
| `HIDDEN_TEXT_KEEP_MIN` | 2.0 | Tương phản `≥` ⇒ giữ; validator `HIDDEN_TEXT_DROP_MAX < HIDDEN_TEXT_KEEP_MIN` | Yêu cầu GĐ0 0B |
| `SCAN_TEXT_LAYER_MODE3_MIN` | 0.50 | Tỉ lệ ký tự render mode 3 `>` ("phần lớn") | Người dùng 2026-10-31 (c); giá trị suy ra |
| `SCAN_TEXT_LAYER_IMAGE_MIN` | 0.90 | `image_area_ratio` (mọi ảnh) `≥` ("ảnh phủ gần hết trang"; cùng giá trị `TEXT_FULL_IMAGE_MIN`) | Người dùng 2026-10-31 (c); giá trị suy ra |
| `OCCLUSION_MIN_COVER` | 0.50 | Tỉ lệ hộp chữ bị che `≥` ⇒ `TEXT_OCCLUDED` | Yêu cầu GĐ0 0B |
| `CHAR_MERGE_MIN_CHARS` | 3 | Số ký tự rời tối thiểu để ghép (`≥`) | Đề xuất suy ra |
| `CHAR_MERGE_BASELINE_TOL` | 0.20 | Lệch baseline tối đa, × chiều cao ký tự (`≤`) | Đề xuất suy ra |
| `CHAR_MERGE_GAP_TOL` | 0.25 | Lệch khoảng cách so với trung vị, × chiều cao ký tự (`≤`) | Đề xuất suy ra |
| `HEADER_FOOTER_MIN_PAGE_RATIO` | 0.60 | Tỉ lệ trang có dòng lặp (`≥`) | Yêu cầu GĐ0 0B |
| `HEADER_FOOTER_MIN_SPREAD` | 0.60 | `(p_max − p_min) / N` (`≥`) | Yêu cầu GĐ0 0B |
| `FIGURE_TEXT_MIN_TOKENS` | 8 | Token lớp chữ đã trừ chuỗi lặp: `≥` ⇒ hình chỉ cắt PNG; `<` là một vế của (e) | Yêu cầu GĐ0 0C |
| `IMAGE_READ_AREA_MIN` | 0.30 | Độ phủ ảnh nội dung `≥`, vế còn lại của (e) | Yêu cầu GĐ0 0C |
| `PAGE_ROLE_MIN_TOKENS` | 8 | Trang có ít token hơn (`<`) không tham gia luật trùng | Yêu cầu GĐ0 0D |
| `PAGE_ROLE_COMMON_TOKEN_RATIO` | 0.60 | Token có trên `≥` tỉ lệ này số trang bị trừ khỏi `T(p)` | Yêu cầu GĐ0 0D |
| `COVER_JACCARD_MIN` | 0.80 | `Jaccard(R)` với trang tài liệu khác `≥` | Yêu cầu GĐ0 0D |
| `CHAPTER_NAV_JACCARD_MIN` | 0.85 | `Jaccard(T)` trong cụm `≥` | Yêu cầu GĐ0 0D |
| `CHAPTER_NAV_MIN_MEDIAN_GAP` | 3 | Trung vị khoảng cách thành viên `≥` | Yêu cầu GĐ0 0D |
| `CHAPTER_NAV_MAX_LINE_CHARS` | 90 | Mọi dòng `≤` | Yêu cầu GĐ0 0D |
| `CHAPTER_NAV_MIN_PAGES` | 2 | Số thành viên tối thiểu của cụm (`≥`) | Đề xuất suy ra |
| `BUILD_STEP_SUBSET_MIN` | 0.90 | Tỉ lệ token của trang `i` có ở trang `i+1` (`≥`) | Yêu cầu GĐ0 0D |
| `BUILD_STEP_POSITION_TOL` | 0.02 | Lệch toạ độ dòng `≤` | Yêu cầu GĐ0 0D |
| `BUILD_STEP_DHASH_SAME_MAX` | 4 | dHash `≤` ⇒ cùng ảnh | Yêu cầu GĐ0 0D |
| `PARALLEL_SERIES_DHASH_MIN` | 9 | dHash `≥` (tức > 8) ⇒ chuỗi ảnh song song; giữa hai ngưỡng ⇒ giữ + cờ; validator `BUILD_STEP_DHASH_SAME_MAX < PARALLEL_SERIES_DHASH_MIN` | Yêu cầu GĐ0 0D |

**Dữ liệu cho bộ dựng khối: font, số đếm độc lập, đoạn kẻ, ảnh cắt, `PageGeometry` (ADR-010 mục 5, 6, 9; duyệt 2026-11-01 (ADR-010)).** Giao thức worker (`document_readers/protocol.py`) là nội bộ, không phải contract API; ghi ở đây vì các PR X04, X07, V02, X11 cài nó. Mọi field mới có mặc định nên thông điệp và JSON cũ vẫn parse được. Không nâng trần nào của ADR-008 (`max_message_bytes` = 8 MiB, `MAX_PAGE_LINES`, `MAX_PAGE_WORDS`); `test_bad_pdf_limits` (§9 I5) giữ nguyên.

```python
# --- hằng trần ở domain/limits.py (đề xuất suy ra (orchestrator)), protocol.py re-export; tên là đề xuất suy ra
FONT_NAME_MAX_CHARS = 64; MAX_PAGE_FONTS = 256; FONT_SIZE_MIN_PT = 1.0; FONT_SIZE_MAX_PT = 1000.0; MAX_PAGE_RULES = 20_000
MAX_PAGE_GEOMETRY_LINES = 10_000; MAX_PAGE_WORDS = 50_000
    # MAX_PAGE_GEOMETRY_LINES: tên riêng để không trùng MAX_PAGE_LINES = 5000 của domain/vault.py (đề xuất suy ra (orchestrator))
MAX_GEOMETRY_JSON_BYTES = 8 * 1024 * 1024        # trần byte JSON của PageGeometry: hằng domain, KHÔNG phải field PdfReaderConfig
MAX_PAGE_BLOCKS = 10_000                          # số khối tối đa mỗi trang; vượt ⇒ LAYOUT_FAILED
# protocol.MAX_PAGE_LINES giữ tên cũ làm bí danh của MAX_PAGE_GEOMETRY_LINES để test cũ chạy (đề xuất suy ra (orchestrator))
# --- giao thức worker (infrastructure/document_readers/protocol.py)

class RawFont(_Frozen):                           # một font của trang (bảng font cấp trang)
    font_name: str | None = Field(default=None, max_length=FONT_NAME_MAX_CHARS)
        # worker: tên PDFium, bỏ tiền tố subset ^[A-Z]{6}\+, chỉ giữ ASCII in được U+0020–U+007E, cắt 64 ký tự; rỗng ⇒ null.
        # Đọc bằng bộ đệm cố định 256 byte (không cấp phát theo độ dài PDF khai báo); cache theo đối tượng font của trang
    bold: bool = False                            # worker: weight ≥ PdfReaderConfig.bold_min_weight (mặc định 600) HOẶC tên chứa
                                                  # token của BOLD_NAME_TOKENS (Bold, Black, Heavy, Semibold; config.py)
    italic: bool = False                          # worker: cờ italic / góc nghiêng ≠ 0 HOẶC tên chứa Italic/Oblique
    monospace: bool = False                       # worker: cờ FixedPitch HOẶC tên thuộc MONOSPACE_NAME_TOKENS (tín hiệu phụ)
    bold_source: Literal["weight", "name"] | None = None        # null ⇔ bold = false
    italic_source: Literal["flag", "name"] | None = None        # null ⇔ italic = false
    monospace_source: Literal["flag", "name"] | None = None     # null ⇔ monospace = false
    # Luật bảng font (đề xuất suy ra (orchestrator)): khử trùng theo (tên làm sạch, bold, italic, monospace); font_id theo thứ tự xuất
    # hiện đầu tiên trên trang. bold_source: weight trước, chỉ xét tên khi weight < bold_min_weight; italic/monospace: cờ
    # trước rồi tên; weight âm/lỗi ⇒ không có tín hiệu weight. Khớp tên theo token tách ở ranh giới CamelCase và dấu phân
    # cách (-, _, dấu phẩy, dấu cách). Đọc font tắt ở lượt quét template và đường OCR

class RawWord(_Frozen):                           # sửa: mọi chuỗi ký tự không trắng tối đa của dòng là một từ
    text: str; bbox: UnitBBox | None              # bbox null khi không ký tự nào của từ có hộp (trước đây từ đó bị bỏ)
        # Từ không hộp KHÔNG được làm trang vượt MAX_PAGE_WORDS: nếu thêm chúng làm vượt trần, worker bỏ mọi từ không hộp của
        # trang và đặt RawPdfPage.boxless_words_dropped = true ⇒ geometry = None ⇒ trang LAYOUT_FAILED; KHÔNG BAO GIỜ làm
        # trang failed (đề xuất suy ra (orchestrator))
    font_id: int | None = Field(default=None, ge=0, lt=MAX_PAGE_FONTS)   # worker: chỉ số vào RawPdfPage.fonts
    font_size_pt: float | None = Field(default=None, ge=FONT_SIZE_MIN_PT, le=FONT_SIZE_MAX_PT)
        # worker: FPDFText_GetFontSize × √|det| (ma trận chữ × ma trận các form chứa nó), kẹp khoảng, không hữu hạn ⇒ null.
        # Từ/dòng: giá trị chiếm đa số ký tự không trắng (cỡ làm tròn 0.1 pt); hoà ⇒ giá trị của ký tự đầu tiên (I4).
        # Ký tự có font_id = null không bỏ phiếu font (vẫn bỏ phiếu cỡ)
class RawTextLine(_Frozen):                       # thêm hai field như RawWord, cùng luật đa số và cùng ràng buộc
    font_id: int | None = Field(default=None, ge=0, lt=MAX_PAGE_FONTS)
    font_size_pt: float | None = Field(default=None, ge=FONT_SIZE_MIN_PT, le=FONT_SIZE_MAX_PT)

class RawRule(_Frozen):                           # đoạn kẻ (X07)
    bbox: UnitBBox                                # worker: hộp sau biến đổi (form XObject), chuẩn hoá [0, 1], gốc trên-trái
    kind: Literal["stroke", "thin_fill"]          # nét ngang/dọc của path; hình chữ nhật tô có cạnh ngắn ≤ rule_max_thickness (0.004)

class RawPdfPage(_Frozen):                        # thêm
    fonts: list[RawFont] = Field(default_factory=list, max_length=MAX_PAGE_FONTS)
        # font thứ 257 trở đi: ký tự của nó có font_id = null
    font_data_dropped: bool = False
        # worker: thông điệp trang vượt max_message_bytes vì field font ⇒ fonts = [], mọi font_id/font_size_pt null
    boxless_words_dropped: bool = False
        # worker: đã bỏ từ không hộp vì vượt MAX_PAGE_WORDS (xem RawWord) ⇒ code không dựng geometry cho trang
    pdfium_nonws_total: int | None = Field(default=None, ge=0)
        # worker: số đếm ĐỘC LẬP theo đơn vị ledger (§5.15) trên dòng ký tự PDFium TRƯỚC lọc chữ ẩn, ghép ký tự rời, dựng dòng:
        # mọi ký tự không tự sinh, tách đoạn tối đa ký tự không trắng liền nhau (ngắt ở ký tự trắng, ký tự tự sinh, ngắt
        # dòng, đổi đối tượng chữ chủ), NFC từng đoạn, cộng số code point không trắng. null với trang không đọc lớp chữ
    hidden_nonws_dropped: int = Field(default=0, ge=0)
        # worker: như trên, chỉ trên ký tự của đối tượng chữ bị loại vì chữ ẩn; đi kèm hidden_chars_dropped (giữ nghĩa cũ:
        # đếm ký tự không tự sinh, kể cả ký tự trắng)
    rules: list[RawRule] = Field(default_factory=list, max_length=MAX_PAGE_RULES)
    rules_over_cap: bool = False
        # worker: vượt MAX_PAGE_RULES, HOẶC thông điệp trang vượt max_message_bytes vì rules (như font_data_dropped, không
        # báo lỗi thông điệp quá lớn; đề xuất suy ra (orchestrator)) ⇒ rules = [], trang không dựng bảng từ đoạn kẻ

class RawOcrPage(_Frozen):                        # thêm (X11)
    ocr_nonws_total: int | None = Field(default=None, ge=0)
        # worker: đơn vị ledger trên từ của TSV Tesseract (mỗi từ có chữ), đếm trước khi gom dòng; null khi không OCR

# --- ảnh cắt: MỘT CropRequest dùng chung (V02 sở hữu qua Protocol RegionRenderer.render_regions(path, requests) ở
# pdf_reader.py; X07, X09 dùng lại). Gửi qua stdin SAU khi dựng khối; worker trả MỘT thông điệp kind = "crop_batch".
class CropRegion(_Frozen):
    region_id: int = Field(ge=0); page_index: int = Field(ge=0); bbox: UnitBBox     # tiến trình cha: bbox của khối
class CropRequest(_Frozen):
    kind: Literal["crop_request"] = "crop_request"
    regions: list[CropRegion] = Field(min_length=1)   # ≤ crop_max_regions_per_request
    max_edge_px: int = Field(ge=64)                   # ≤ crop_max_edge_px (1024); trần cạnh của ảnh gửi VLM chốt ở X02b
class CropPng(_Frozen):
    region_id: int; width_px: int; height_px: int; size_bytes: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$"); png_base64: str
class CropError(_Frozen):
    region_id: int
    code: Literal["render_error", "over_image_bytes", "over_message_bytes", "over_budget", "bad_region"]
class CropBatchMessage(_Frozen):
    kind: Literal["crop_batch"] = "crop_batch"
    crops: list[CropPng] = []; errors: list[CropError] = []   # mỗi region_id xuất hiện đúng một lần ở một trong hai danh sách
```

- **Trần của `CropRequest`** (field `PdfReaderConfig`, đề xuất suy ra): `crop_max_regions_per_request = 32`, `crop_max_per_page = 64`, `crop_max_per_document = 3000`, `crop_max_edge_px = 1024`, `crop_max_image_bytes = 1 MiB`, `crop_margin = 0.005` (nới lề hộp), `crop_request_timeout_s = 30.0` (ngân sách một lượt; hết hạn ⇒ vùng còn lại `over_budget`); `crop_document_budget_s = 120.0` (ngân sách tổng cho mọi lượt cắt của MỘT tài liệu, validator ≤ `document_timeout_s` của bộ nhập; hết ngân sách ⇒ mọi vùng còn lại `crop_capped`; đường dựng lại sau PATCH dùng cùng ngân sách; đề xuất suy ra (orchestrator)). Worker cắt ảnh chạy với CÙNG giới hạn OS, CPU, bộ nhớ của worker PDF (ADR-008 mục 3; Job Object trên Windows, `RLIMIT_*` trên Unix) (đề xuất suy ra (orchestrator)). Validator cấu hình: `crop_max_edge_px` ≤ 1024 và `crop_max_image_bytes` ≤ 1 MiB (trần của `CropImage`, §5.15; đề xuất suy ra (orchestrator)). Khối `bbox = null` không có vùng cắt, không mang `crop_pending`. Trần byte tính trên KÍCH THƯỚC THÔNG ĐIỆP đã mã hoá (JSON chứa base64, kèm tiền tố và `\n`) so với `max_message_bytes` (ADR-010 mục 9 (t')): worker dừng thêm ảnh khi ảnh kế tiếp làm thông điệp vượt trần. Tiến trình cha kiểm lại từng ảnh: base64 chuẩn, `size_bytes` khớp, magic PNG, IHDR khớp `width_px`/`height_px`, cạnh dài ≤ `max_edge_px`, sha256 khớp (danh sách kiểm cho ảnh cắt chốt tại đây; kiểm và trần riêng của ảnh gửi VLM, gồm `HOLOCRON_VLM_MAX_EDGE_PX`, chốt ở X02b: ADR-009 bản cập nhật, merge SAU ADR-010; tới khi đó đường VLM không bật); sai ⇒ coi như `render_error`. Vượt trần số vùng của tài liệu/trang ⇒ khối nhận `crop_capped`, lỗi khác ⇒ `crop_failed`; khối giữ `crop_pending`, luồng nhập không ném lỗi.

```python
# --- domain/documents.py (X04 tạo; X07 thêm rules, X11 thêm ocr_lines/ocr_nonws_total). Từ migration 0007 lưu ở cột
# pages.geometry_json (§5.13); KHÔNG có trong PageExtraction hay response API (GET …/pages/{n} trả PageExtraction như cũ).
GEOMETRY_VERSION = 1                              # hằng; tăng khi worker đo khác (cấu hình tầng đọc, ADR-010 mục 15 (g'''))

class GeoFont(StrictModel):                       # chép RawFont
    font_name: str | None; bold: bool; italic: bool; monospace: bool
    bold_source: Literal["weight", "name"] | None; italic_source: Literal["flag", "name"] | None
    monospace_source: Literal["flag", "name"] | None

class GeoWord(StrictModel):
    text: str = Field(min_length=1)               # worker: chữ của từ (chưa NFC; ledger và id áp NFC)
    bbox: BBox | None                             # worker
    font_id: int | None = None; font_size_pt: float | None = None   # worker (RawWord)
    conf: float | None = Field(default=None, ge=0.0, le=1.0)        # chỉ từ OCR: conf Tesseract / 100

class GeoLine(StrictModel):
    words: list[GeoWord]                          # worker: theo thứ tự trong dòng
    bbox: BBox | None; font_id: int | None = None; font_size_pt: float | None = None
    flags: list[Literal["LOW_TEXT_CONTRAST", "TEXT_OCCLUDED"]] = []  # worker (GĐ0 0B)
    header_footer: bool = False                   # worker (GĐ0 0B(5))
    extracted_line_indices: list[int]
        # code (tiers.assemble_text_layer_page): các ExtractedLine.line_index mà tiers._chunks tạo từ dòng thô này (dòng thô
        # dài bị cắt thành nhiều ExtractedLine; dòng chỉ gồm khoảng trắng: words = [] và extracted_line_indices = []).
        # Dòng OCR: chỉ số ExtractedLine tương ứng. Cờ FONT_ENCODING_SUSPECT (tính ở tiers.py trên ExtractedLine) lấy qua
        # ánh xạ này để gắn cờ khối font_encoding_suspect

class GeoRule(StrictModel):
    bbox: BBox; kind: Literal["stroke", "thin_fill"]   # worker (RawRule)

class PageGeometry(StrictModel):
    version: int = Field(ge=1)                    # code: GEOMETRY_VERSION lúc đọc
    reader_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
        # code: sha256 JSON chuẩn hoá của TOÀN BỘ PdfReaderConfig (cấu hình tầng đọc; đề xuất suy ra (orchestrator))
    source: Literal["text_layer", "ocr"]          # code: tier_used của trang
    lines: list[GeoLine] = Field(default=[], max_length=MAX_PAGE_GEOMETRY_LINES)   # worker: dòng thô lớp chữ (không gian chỉ số của line_refs)
    fonts: list[GeoFont] = Field(default=[], max_length=MAX_PAGE_FONTS)
    rules: list[GeoRule] = Field(default=[], max_length=MAX_PAGE_RULES)   # X07
    rules_over_cap: bool = False; font_data_dropped: bool = False
    ocr_lines: list[GeoLine] = Field(default=[], max_length=MAX_PAGE_GEOMETRY_LINES)   # X11: dòng Tesseract có từ và toạ độ
    pdfium_nonws_total: int | None = None; hidden_nonws_dropped: int = 0; ocr_nonws_total: int | None = None   # worker
    # validator: tổng số từ (lines + ocr_lines) ≤ MAX_PAGE_WORDS (50 000); font_id < len(fonts). Code dựng geometry kiểm
    # thêm JSON ≤ MAX_GEOMETRY_JSON_BYTES; vượt bất kỳ trần nào ⇒ geometry = None (trang vẫn đọc bình thường bằng dòng)
```

- `PageGeometry` chỉ có ở trang đọc xong không `failed`; trang lưu trước khi có cột `pages.geometry_json` không có (cột null).
- **Tách khỏi `PageExtraction` (ADR-010 mục 5; đề xuất suy ra, làm rõ Phụ lục A3 (p'')):** `PageExtraction` KHÔNG có field geometry, nên `response_model=PageExtraction` của `routers/documents.py` và `model_dump_json()` vào `pages.extraction_json` không đổi. Hàm mới `tiers.assemble_text_layer_page_read` (và đường OCR ở X11) trả model nội bộ `PageRead {extraction: PageExtraction, geometry: PageGeometry | None}` (tên đề xuất suy ra; không ra response); `tiers.assemble_text_layer_page` giữ chữ ký cũ và trả `.extraction` (đề xuất suy ra (orchestrator)). Geometry tới service qua tham số keyword-only `geometry_sink` của `WorkerPdfReader.read_pages` (gọi với `page_number` 1-based TRƯỚC khi yield trang; lỗi trong sink lan ra), không thuộc `Protocol` `PdfReader` mà thuộc `Protocol` RIÊNG `PageGeometrySource` cùng module với `PdfReader`: `read_pages(<cùng tham số của PdfReader.read_pages>, *, geometry_sink: GeometrySink)`, `GeometrySink = Callable[[int, PageGeometry | None], None]`; `WorkerPdfReader` thoả cả hai; service nhận `PageGeometrySource` qua tham số/`Depends`, không `isinstance`/`cast`; test cấp stub (đề xuất suy ra (orchestrator)). Từ X04 tới trước khi migration 0007 (X09) merge: service chỉ giữ geometry trong bộ nhớ của lần đọc rồi bỏ, KHÔNG lưu, KHÔNG ra response; tài liệu nhập trong khoảng đó có `layout_version = null` (đi `build_chunks`). Từ X09: repository ghi geometry vào `pages.geometry_json` bằng method riêng, cùng giao dịch với `extraction_json` của trang.
- **Sau `PATCH …/lines`** (ADR-010 mục 13): `PageGeometry` không đổi; khối của trang được dựng lại từ dòng đã xác nhận (§5.15 "Dựng lại"), ledger chuyển `ledger_basis = "confirmed_lines"`.

### 5.8 Knowledge Vault (ADR-006, ADR-007)

`Citation`, `DocumentMeta`, `KnowledgeChunk` đã có trong `domain/schemas.py`; chép nguyên văn, kèm thay đổi:

```python
class Citation(StrictModel):                     # frozen
    doc_id: str = Field(min_length=3, max_length=128)
    page_number: int = Field(ge=1)
    verbatim_quote: str = Field(min_length=8, max_length=600)   # validator gộp khoảng trắng
    chunk_id: str | None = None

class SourceKind(str, Enum):
    TEXTBOOK = "textbook"; LECTURE_SLIDES = "lecture_slides"; PAST_EXAM = "past_exam"; LAB_MANUAL = "lab_manual"
    STUDY_GUIDE = "study_guide"                  # đề xuất: đề cương ôn tập (danh sách câu hỏi không có đáp án)

class DocumentMeta(StrictModel):
    doc_id: str = Field(min_length=3, max_length=128)       # code: "doc_" + sha256[:16], như UploadedDocument
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # người dùng (form của §6.5)
    title: str = Field(min_length=1, max_length=300)        # người dùng; mặc định tên file bỏ đuôi
    source_kind: SourceKind                                 # người dùng
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")          # code
    page_count: int = Field(ge=1)                           # pypdfium2, ở bước thử mở của §6.5
    has_text_layer: bool                                    # code: true khi ≥ 1 trang dùng tầng 1
        # đề xuất: đổi thành bool | None, null khi còn trang pending
    uploaded_at: datetime = Field(default_factory=_utcnow)

class KnowledgeChunk(StrictModel):
    chunk_id: str = Field(default_factory=lambda: _new_id("chk"))   # code
    doc_id: str = Field(min_length=3, max_length=128)
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # chép từ DocumentMeta
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
        # code: chép từ DocumentImport.chapter_code (người dùng nhập ở form §6.5, hoặc từ 2026-10-31 code chọn theo
        # §5.7 "Chương tự động") khi cắt chunk; người dùng sửa qua
        # PATCH /v1/vault/chunks/{id}/labels (§6.6) (duyệt 2026-10-26; ADR-006 mục 2 đã sửa theo, duyệt 2026-10-26
        # chiều, §17 D9). LLM (nếu có) chỉ đề xuất đổi chương cho từng chunk, người dùng xác nhận
    page_number: int = Field(ge=1)
    page_end: int | None = Field(default=None, ge=1)
    raw_text: str = Field(min_length=1)          # code: văn bản người dùng đã xác nhận ở §6.5, cắt theo §12.1
    verbatim_quote: str = Field(min_length=8, max_length=600)
        # LLM đề xuất câu tiêu biểu; không có key thì code lấy câu đầu tiên đủ 8 ký tự (đề xuất). Luôn qua kiểm I3
    category: ChunkCategory
        # code: mặc định THEORY khi cắt chunk từ tài liệu xác nhận ở §6.5 (duyệt 2026-10-26); người dùng sửa qua
        # PATCH …/labels (§6.6); LLM có thể đề xuất khi có key (§6.5 "Xác nhận")
    tags: list[str] = []                         # code: #theory / #practice_example theo category
    topic_keys: list[str] = []
        # code: [] khi cắt chunk (duyệt 2026-10-26); người dùng đặt qua PATCH …/labels (§6.6); LLM có thể đề xuất
        # khi có key (§6.5 "Xác nhận"). Validator: mỗi khoá khớp TOPIC_KEY_PATTERN; danh sách được sắp và bỏ trùng.
        # Giới hạn ở biên vào: đề xuất (chờ duyệt), ghi theo code hiện có; xem ghi chú dưới khối mã
    linked_chunk_ids: list[str] = []             # code (§12.1)
    token_count: int = Field(ge=1)               # code
    embedding_model: str | None = None           # luôn null trong MVP (ADR-006 mục 4)
    label_status: Literal["proposed", "confirmed"] = "proposed"
        # code: trạng thái xác nhận nhãn (field duyệt 2026-10-26 chiều, §17 D8). Chunk mới cắt ở §6.5 nhận
        # "proposed"; PATCH …/labels đặt "confirmed" (quy tắc duyệt 2026-10-26)
    mapping_confidence: float | None = Field(default=None, ge=0.0, le=1.0)   # đề xuất: độ tin cậy LLM trả cho nhãn
    version: int = Field(default=1, ge=1)        # đề xuất: optimistic locking khi sửa nhãn (§6.6)
    is_exercise: bool | None = None
        # LLM (thêm 2026-10-31, GĐ1 1C, duyệt 2026-10-31): nhãn "chunk là bài tập" LLM trả trong job đề xuất cặp (§6.6);
        # null khi chưa có LLM gắn, hoặc LLM thiếu nhãn, trả null hay trả nhãn xung đột cho chunk (§5.8 giao thức con trỏ). Chỉ dùng để KHÔNG tích sẵn cặp của chunk đó (ADR-007 mục 3); không bỏ chunk, không
        # bỏ trang, không đổi label_status. Ghi nhãn tăng version của chunk (đề xuất suy ra). Cột chunks.is_exercise (§5.13)
    # --- thêm 2026-11-01 (ADR-010 mục 16; duyệt 2026-11-01 (ADR-010); tên là đề xuất suy ra). Chunk cũ và chunk cắt bằng
    # build_chunks: null / []. Cột chunks.section_id, chunks.block_ids_json (migration 0007, §5.13)
    section_id: str | None = Field(default=None, pattern=r"^sec_[0-9a-f]{16}$")
        # code: Section (§5.15) chứa mọi khối của chunk; chunk không vắt qua hai Section
    block_ids: list[str] = []
        # code: các block_id (§5.15) tạo nên raw_text, theo thứ tự nối; mỗi phần tử khớp ^blk_[0-9a-f]{16}$
```

- **Vault theo khối (ADR-010 mục 13, 16; duyệt 2026-11-01 (ADR-010); thay ADR-006 mục 3 cho tài liệu có khối).** Tài liệu có `DocumentImport.layout_version` khác null cắt chunk theo khối/mục ở §12.1; `raw_text` = `text` các khối đủ điều kiện nối bằng `\n\n`, nên I3 giữ nguyên cơ chế (chuỗi con sau NFC + gộp khoảng trắng). Nguồn chữ của chunk CHỈ là tập khối ứng viên ở bước cắt chunk của settle, trùng tập đủ điều kiện khi tài liệu đã đóng (§5.15): trang đã xác nhận, không bỏ mềm; khối `vlm` chỉ tồn tại sau cổng đối chiếu OCR; `figure`/`table` (`text = null`), `header_footer`, `page_number` không thành chunk. Tài liệu nhập trước (`layout_version = null`) giữ `raw_text` và chunk đã lưu, không tự cắt lại (I4).

- **Giới hạn `topic_keys` (đề xuất (chờ duyệt), ghi theo code hiện có; §17 D8):** mỗi khoá khớp `TOPIC_KEY_PATTERN = ^[a-z0-9_]+(\.[a-z0-9_]+){2,}$` (`domain/schemas.py`) và dài tối đa `MAX_TOPIC_KEY_CHARS = 128` ký tự; mỗi danh sách tối đa `MAX_TOPIC_KEYS = 32` khoá (`domain/vault.py`, kiểu `TopicKey`). Hai giới hạn độ dài và số lượng được cưỡng chế ở biên vào: `ConfirmedDocumentText.topic_keys` (`domain/vault.py`), body `PATCH /v1/vault/chunks/{chunk_id}/labels` (`ChunkLabelsPatch.topic_keys`, `routers/vault.py`) và tham số lặp `topic_key` của `GET /v1/vault/chunks` và `GET /v1/vault/search` (§6.6). Vượt giới hạn ⇒ 422. Bản thân `KnowledgeChunk.topic_keys` (`domain/schemas.py`) chỉ kiểm mẫu, sắp và bỏ trùng, chưa kiểm hai giới hạn này.
- **I3 (đã duyệt 2026-10-24):** `KnowledgeChunk._consistency` kiểm `verbatim_quote` là chuỗi con của `raw_text` sau khi đưa cả hai về NFC rồi gộp khoảng trắng. Code hiện chỉ gộp khoảng trắng; phần NFC thêm cùng test `test_chunk_accepts_quote_in_nfd_against_nfc_text` (`tests/test_schemas_and_gate.py`). `test_chunk_rejects_quote_not_in_text` vẫn phải xanh mà không sửa.
- Đề xuất: `VaultCitationProvider` chỉ dùng chunk có `label_status = "confirmed"`, vì `topic_keys` quyết định chunk nào được trích dẫn.

**Cặp khái niệm** (ADR-007 mục 1; tên field theo ADR, phần còn lại là đề xuất):

```python
class ConceptPairStatus(str, Enum):
    PROPOSED = "proposed"; CONFIRMED = "confirmed"; REJECTED = "rejected"

class ConceptPair(StrictModel):
    pair_id: str = Field(default_factory=lambda: _new_id("cp"))   # code
    chunk_id: str                                # chunk chứa trích dẫn
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # chép từ chunk
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")   # chép từ chunk
    term: str = Field(min_length=1, max_length=120)             # LLM đề xuất (khi có LLM) hoặc người dùng nhập; người dùng sửa được
    definition_quote: str = Field(min_length=8, max_length=600)
        # LLM đề xuất hoặc người dùng bôi trong chunk; PHẢI là chuỗi con của chunk.raw_text (NFC + gộp khoảng trắng, I3)
    term_spans: list[tuple[int, int]] = []       # code: vị trí term trong definition_quote (NFC) để che bằng ____
    status: ConceptPairStatus
    proposed_by: Literal["llm", "user"]
    mapping_confidence: float | None = Field(default=None, ge=0.0, le=1.0)   # LLM; null khi người dùng tự tạo
    version: int = Field(default=1, ge=1)        # code: optimistic locking
    created_at: datetime
    confirmed_at: datetime | None = None
    # --- thêm 2026-10-31 (GĐ1 1C; ADR-007 mục 3, duyệt 2026-10-31). Cặp cũ và cặp người dùng tự tạo: false / [].
    pre_selected: bool = False
        # code: máy tích sẵn ô "Chấp nhận" trên màn hình; KHÔNG đổi status (vẫn "proposed" tới khi người bấm Chấp nhận).
        # true chỉ khi failed_gates rỗng VÀ luật tích sẵn đang bật (PAIR_PRESELECT_ENABLED, dưới đây)
    failed_gates: list[PairGate] = []
        # code: các cổng trượt, sắp theo thứ tự khai báo, không trùng; không rỗng ⇒ cặp vào nhóm "Cần kiểm tra"
```

```python
# thêm 2026-10-31 (GĐ1 1C; ADR-007 mục 3, duyệt 2026-10-31; tên mã là đề xuất suy ra)
PairGate = Literal[
    "G1_SOURCE",       # nguồn đáng tin
    "G2_GLYPH",        # glyph nghi lỗi font
    "G3_OCCLUDED",     # chữ bị che hoặc tương phản thấp
    "G4_COLUMN_MIX",   # trộn cột
    "G5_NUMBERS",      # số không khớp
    "EXERCISE",        # chunk là bài tập (is_exercise = true) hoặc trang page_role = "exercise"
]
```

- **Luật tích sẵn TẠM (`preselect_page_flags_v1`; duyệt hướng 2026-10-31, ADR-007 mục 3).** Trước khi có G1–G5 đầy đủ (cần cấp khối, sau GĐ0), mỗi cổng xét trên các TRANG nguồn của chunk (`page_number` tới `page_end`) bằng cờ cấp trang hiện có; đây là luật tạm và được thay khi có G1–G5 cấp khối. G1 trượt khi một trang nguồn không qua luật độ tin cậy nguồn `source_trust_v1` (§5.7 "Tự xác nhận cấp trang"; CÙNG hàm với luật tự xác nhận `page_clean_v1`, không viết lại điều kiện); G2 trượt khi một trang nguồn có `FONT_ENCODING_SUSPECT`, hoặc `definition_quote` chứa U+FFFD hay ký tự PUA; G3 trượt khi một trang nguồn có `TEXT_OCCLUDED` hoặc `LOW_TEXT_CONTRAST`; G4 trượt khi một trang nguồn có `has_table = true`; G5 trượt khi một trang nguồn có `cross_check.status` khác `ok` và `not_applicable`; `EXERCISE` trượt khi `is_exercise` của chunk KHÁC `false` (tức `true`, hoặc `null` vì LLM chưa gắn, thiếu nhãn hay trả nhãn xung đột; an toàn mặc định), hoặc một trang nguồn có `page_role = "exercise"`. Hệ quả: cặp từ endpoint đồng bộ `propose` (không gắn `is_exercise`) không bao giờ được tích sẵn trừ khi chunk đã có nhãn `false` từ một job. Trang nguồn không còn trong kho (tài liệu đã xoá) ⇒ G1 trượt. Ánh xạ từng cổng sang cờ trang là đề xuất suy ra.
- **Bật luật tích sẵn:** `PAIR_PRESELECT_ENABLED` (field cấu hình ở `infrastructure/`, mặc định `false`) chỉ được bật khi luật đang dùng đạt precision ≥ 90% trên mẫu chấm, và số đo được ghi ở ADR-007 mục 3; không đạt thì thu hẹp luật (ví dụ chỉ tích cặp mà cả LLM lẫn mẫu cấu trúc cùng tìm ra) rồi đo lại. Khi tắt, `pre_selected` luôn `false` nhưng `failed_gates` vẫn được tính (đề xuất suy ra: mặc định tắt là phương án an toàn vì chưa có số đo).
- `failed_gates` và `pre_selected` tính một lần lúc lưu cặp, không tính lại khi trang hay chunk đổi sau đó (đề xuất suy ra). LLM KHÔNG BAO GIỜ xác nhận cặp; `pre_selected` không phải xác nhận.
- Cặp có trích dẫn không khớp nguyên văn bị loại ngay ở service, không lưu và không hiện cho người dùng (ADR-007 mục 1 bước 2).
- Chỉ cặp `confirmed` vào nhóm ứng viên và vào `pool_sha256` (ADR-007 mục 2). Sửa `term` hay `definition_quote` của cặp đã xác nhận làm đổi `pool_sha256`, nên câu lý thuyết sinh sau đó đổi theo; twin đã sinh giữ nguyên (trích dẫn là bản chụp, ADR-006 mục 7).

**Job nền đề xuất cặp (GĐ1 1C; ADR-007 mục 3, duyệt 2026-10-31).** Endpoint ở §6.6. Endpoint đồng bộ `POST /v1/vault/concept-pairs/propose` giữ nguyên contract và giao thức `{term, definition_quote, chunk_id}` cũ (tương thích); job dùng giao thức con trỏ dưới đây.

```python
ProposalJobStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    # code: succeeded = mọi lô xong không lỗi; partial = có lô lỗi và có lô xong; failed = mọi lô lỗi;
    # cancelled = người dùng huỷ trước khi hết lô (đề xuất suy ra)
ProposalRejectReason = Literal[                  # = RejectReason hiện có của services/concept_proposal.py, thêm 6 mã con trỏ
    "quote_not_verbatim", "unknown_chunk", "invalid_term", "invalid_quote", "duplicate", "chunk_limit", "proposal_limit",
    "unknown_span",          # id span không có trong lô
    "span_cross_chunk",      # def_span_ids không liền nhau hoặc khác chunk với nhau
    "term_span_cross_chunk", # term_span_id thuộc chunk khác chunk của def_span_ids (thêm 2026-10-31 theo review)
    "term_not_in_span",      # term_exact không phải chuỗi con của span term_span_id
    "anchor_not_found",      # mốc đầu/cuối không có trong các span đã chọn, sau lần xin mốc dài hơn
    "anchor_ambiguous",      # mốc xuất hiện nhiều hơn một lần, sau lần xin mốc dài hơn
]
BatchErrorKind = Literal["llm_upstream", "deadline", "format"]   # lỗi của cả một lô (502 tương ứng ở endpoint đồng bộ)

class ProposalJob(StrictModel):
    job_id: str = Field(pattern=r"^pjob_[0-9a-f]{16}$")      # code
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # request hoặc chép từ tài liệu
    trigger: Literal["document_confirmed", "manual"]         # code
    document_id: str | None                                  # request/code; null khi gọi tay theo chunk_ids
    status: ProposalJobStatus                                # code
    chunk_count: int = Field(ge=1)                           # code: số chunk của job (sau bỏ trùng)
    batch_size: int = Field(ge=1, le=20)                     # cấu hình PROPOSAL_JOB_BATCH_CHUNKS
    batches_total: int = Field(ge=1)                         # code: ceil(chunk_count / batch_size)
    batches_done: int = Field(ge=0)                          # code: lô chạy xong (kể cả lô không ra cặp nào)
    batches_failed: int = Field(ge=0)                        # code: lô lỗi; không làm hỏng lô khác
    batch_errors: dict[BatchErrorKind, int] = {}             # code: đếm lô lỗi theo loại
    proposed_count: int = Field(ge=0)                        # code: số cặp đã lưu (status = proposed)
    pre_selected_count: int = Field(ge=0)                    # code: số cặp đã lưu có pre_selected = true
    rejected_count: int = Field(ge=0)                        # code: tổng đề xuất bị loại
    rejected_by_reason: dict[ProposalRejectReason, int] = {} # code; tổng các giá trị = rejected_count
    exercise_chunks: int = Field(ge=0)                       # code: số chunk LLM gắn is_exercise = true
    cancel_requested: bool = False                           # người dùng (POST …/cancel)
    created_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None   # code
```

- **Khi nào chạy:** tự tạo ngay sau khi một tài liệu thành `confirmed` và đã cắt chunk (bước chốt §5.7; `trigger = "document_confirmed"`, mọi chunk của tài liệu), nếu khả năng `concept_pair_llm_proposal` khả dụng (§11.4); không khả dụng thì không tạo job nào, chỉ ghi log (đề xuất suy ra), và `DocumentImport.proposal_job_absent_reason = "llm_unavailable"` (§5.7; thêm 2026-11-01, T1b).
- **Một lần cho mỗi lần đóng tài liệu (T1b; thêm 2026-11-01, đề xuất suy ra, chờ xác nhận):** job tự tạo đúng ở lần bước chốt chuyển tài liệu từ chưa `confirmed` sang `confirmed` (mọi đường chốt: `POST /v1/documents/{document_id}/confirm`, xác nhận trang cuối, cuối lần đọc với tài liệu không có trang tự xác nhận). Quyết định "vừa đóng" được lấy dưới khoá của tài liệu, nên hai lời gọi đồng thời chỉ tạo một job; lời gọi `submit` chạy sau khi nhả khoá. Gọi lại xác nhận trên tài liệu đã `confirmed` (kể cả đường `SETTLE_PENDING` chỉ chạy lại bước chốt mà tài liệu đã đóng) KHÔNG tạo job mới. Tài liệu đã đóng mà chưa có job (`not_retained`, `llm_unavailable` rồi cấu hình LLM) chỉ chạy lại bằng tay (§6.6).
- **Lỗi job không làm hỏng xác nhận:** `submit` lỗi (runtime đã đóng, lỗi bất kỳ) ⇒ ghi log, xác nhận vẫn trả 200 với tài liệu `confirmed` và chunk đã cắt; lô lỗi về sau chỉ đổi `ProposalJob.status`/`batches_failed`, không đổi trạng thái tài liệu, trang hay chunk. Gọi tay qua §6.6 (`trigger = "manual"`); không khả dụng ⇒ 409 `CapabilityError` như endpoint đồng bộ. Trích cặp bằng luật offline (không LLM) thuộc GĐ2, chưa có.
- **Chạy:** chia chunk thành lô ≤ `PROPOSAL_JOB_BATCH_CHUNKS` (mặc định 5, trần 20; người dùng duyệt 2026-11-01, lý do: lô 12–20 chunk hỏng với `max_tokens` 4096 trên tài liệu thật); chạy song song tối đa `PROPOSAL_JOB_MAX_PARALLEL` lô (mặc định 2) trên toàn app; job xếp hàng FIFO. Mỗi lô dùng cùng giới hạn `LlmTaskLimits` (hạn tổng, số lần gọi lại khi JSON lỗi, trần số đề xuất) như endpoint đồng bộ. Lỗi một lô (LLM lỗi, hết hạn, JSON hỏng sau mọi lần thử) chỉ tăng `batches_failed`, không làm hỏng lô khác. Hai cấu hình trên là field có giá trị mặc định ở `infrastructure/llm_config.py` (cùng chỗ `LlmTaskLimits`), không phải biến môi trường (đề xuất suy ra).
- **Huỷ:** đặt `cancel_requested`; lô chưa bắt đầu không chạy; lô đang chạy được chạy hết (không ngắt lời gọi HTTP) và cặp của nó vẫn được lưu; job thành `cancelled` (đề xuất suy ra).
- **Lưu trạng thái job:** trong bộ nhớ của tiến trình, giữ trong một runtime cấp qua `Depends` (như `ImportRuntime`), không phải dict cấp module (CLAUDE.md §2.2); tắt app thì mất job (cặp đã lưu vẫn còn), không tự chạy lại. Không thêm bảng hay migration cho job (đề xuất suy ra: ít đổi contract nhất). Job đã kết thúc giữ tối đa 100 job gần nhất.
- **Giao thức con trỏ (I3):** lô gửi các dòng không rỗng của `raw_text` từng chunk (tách theo `\n`), mỗi dòng một id `S1..Sn` đánh số liên tục trong lô, kèm `chunk_id`. LLM trả một object `{"chunks": [{chunk_id, is_exercise}], "pairs": [...]}` (đề xuất suy ra): `chunks` PHẢI có nhãn `is_exercise` (true/false) cho MỌI chunk của lô; mỗi phần tử của `pairs` có dạng `{term_span_id, term_exact, def_span_ids, def_start_words, def_end_words}`. Chunk thiếu nhãn, nhãn `null`, hoặc nhãn xung đột (cùng `chunk_id` hai giá trị khác nhau) ⇒ lưu `is_exercise = null`, nên cổng `EXERCISE` trượt cho mọi cặp của chunk đó (an toàn mặc định, §5.8). Python làm:
  1. `def_span_ids` phải là các id liền nhau tăng dần của CÙNG một chunk; văn bản đã chọn `W` = đoạn `raw_text` từ đầu span đầu tới cuối span cuối (chuỗi con nguyên văn của `raw_text`).
  2. Mốc: `def_start_words` phải xuất hiện ĐÚNG MỘT lần trong `W`; `def_end_words` phải xuất hiện đúng một lần trong phần `W` sau vị trí bắt đầu. So sau NFC; một dãy khoảng trắng trong mốc khớp một dãy khoảng trắng bất kỳ trong `W` (đề xuất suy ra). Trích dẫn = `W[đầu mốc đầu : cuối mốc cuối]`, cắt từ `raw_text`, không ghép hay sửa chữ.
  3. Mốc không thấy hoặc không duy nhất ⇒ gửi lại LLM MỘT lần (một lời gọi cho mọi phần tử hỏng của lô) xin mốc dài hơn; vẫn hỏng ⇒ loại, đếm `anchor_not_found` hoặc `anchor_ambiguous`.
  4. `term_span_id` phải thuộc CÙNG chunk với `def_span_ids`, không thì loại (`term_span_cross_chunk`); `term_exact` phải là chuỗi con (sau NFC) của span `term_span_id`, không thì loại (`term_not_in_span`); `term` = `term_exact` đã gộp khoảng trắng.
  5. Trích dẫn đã cắt VẪN qua cổng hiện có: kiểm nguyên văn I3 (`quote_in_text`, NFC + gộp khoảng trắng), độ dài 8–600, bỏ trùng, trần mỗi chunk; rồi tính `failed_gates` và `pre_selected`; lưu ở `status = "proposed"`, `proposed_by = "llm"`.

**`CitationProvider.citation_for` (đổi interface nội bộ, đã duyệt 2026-10-25).** ADR-006 mục 6 yêu cầu chọn chunk cùng `subject_code` với blueprint, nhưng chữ ký hiện tại `citation_for(self, slot: BlueprintSlot)` không có `subject_code`. Chữ ký mới: `citation_for(self, subject_code: str, slot: BlueprintSlot) -> Citation | None`. Đây là `Protocol` trong `services/twin_batch.py`, không đổi API. Người dùng đồng ý (CLAUDE.md §7) cho đúng một thay đổi ở test sẵn có, với ba điều kiện:
- chỉ sửa chữ ký của stub `FixedCitations` trong `tests/test_twin_batch.py`, không đổi assertion nào;
- thay đổi đó nằm trong một commit riêng;
- thêm test mới cho việc lọc theo môn: chunk của môn khác không bao giờ được chọn làm trích dẫn, kể cả khi `topic_keys` khớp.

Slot lý thuyết không gọi `CitationProvider`: trích dẫn là `definition_quote` của cặp đúng (ADR-006 mục 6).

### 5.9 Blueprint và `question_kind` (ADR-005 mục 1; F11)

Model đã có trong `domain/schemas.py`; chép nguyên văn, kèm thay đổi và nguồn của từng field khi blueprint được dựng bởi `POST /v1/blueprints` (§6.7).

**Hai lớp contract (duyệt 2026-10-28, quyết định 14(b)):** các khối `BlueprintSlot`, `ExamBlueprint` bên dưới (có `question_kind`, `slots: list[BlueprintSlot]`, slot lý thuyết là một `BlueprintSlot` với các field nullable) là **mục tiêu sau 29/10 (nợ ADR-004)**. Bản đã triển khai ở nhánh B5a là "Contract đã triển khai" ngay sau `ExamBlueprint`: slot lý thuyết là model riêng `TheorySlot`, `ExamBlueprint.slots` là union của `BlueprintSlot` (tính toán, giữ nguyên để `examples/` không đổi) và `TheorySlot`.

```python
class DistractorArchetype(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)   # mã trong QuestionTemplate.misconceptions
    description: str = Field(min_length=5, max_length=400)               # = MisconceptionSpec.label_vi
    observed_option_key: OptionKey | None = None
    observed_option_text: str | None = None
    reproduction_confidence: float = Field(ge=0.0, le=1.0)

class SelfSolveResult(StrictModel):
    status: Literal["MATCHED", "NO_MATCH", "AMBIGUOUS", "SKIPPED"]
    solved_answer_display: str | None = None
    matched_option_key: OptionKey | None = None
    solver_confidence: float = Field(ge=0.0, le=1.0)
    supporting_citations: list[Citation] = []
    solver_template_id: str | None = None

class BlueprintSlot(StrictModel):
    slot_index: int = Field(ge=1)                # code: theo thứ tự item_ids rồi theory_slots, từ 1
    source_page_number: int = Field(ge=1)        # item.page_number
        # đổi thành int | None (duyệt 2026-10-26); slot lý thuyết lấy page_number của source_chunk_id, không có thì null
    format: QuestionFormat                       # item: MCQ4 (item luôn có 4 phương án); lý thuyết: TheorySlotSpec.format (MCQ4 hoặc ESSAY, §5.14)
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
        # item: item.labels.chapter_code; lý thuyết: TheorySlotSpec.chapter_code (người dùng)
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)
        # item.topic_key. Đổi thành str | None, null với slot lý thuyết (duyệt 2026-10-26)
    cognitive_level: CognitiveLevel
        # item: item.labels.cognitive_level (LLM đề xuất, người dùng xác nhận; mặc định "apply" khi không có key);
        # lý thuyết: TheorySlotSpec.cognitive_level (mặc định "remember", ADR-007 Hệ quả)
    question_kind: Literal["computational", "theory"]   # đổi theo ADR-005 mục 1, thay is_computational (duyệt 2026-10-25)
        # code: item_ids ⇒ "computational", theory_slots ⇒ "theory" (phần trỏ tới theory_slots: duyệt 2026-10-26, §17 D8)
    non_cloneable: bool = False                  # item.non_cloneable (luôn false vì §6.7 trả 409 nếu true)
    non_cloneable_reason: str | None = None
    distractor_archetypes: list[DistractorArchetype] = Field(default_factory=list, max_length=3)
        # item: sandbox + code, xem dưới; lý thuyết: [] (mã lấy từ THEORY_MISCONCEPTIONS lúc sinh, I2)
    self_solve: SelfSolveResult                  # item.self_solve (MATCHED vì item đã duyệt)
        # đổi thành SelfSolveResult | None, null với slot lý thuyết (duyệt 2026-10-26)
    mapping_confidence: float = Field(ge=0.0, le=1.0)
        # item: item.labels.mapping_confidence; lý thuyết: 1.0 (người dùng chọn chương)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW
        # đề xuất: APPROVED với slot từ item đã duyệt; NEEDS_REVIEW với slot lý thuyết

class ExamBlueprint(StrictModel):
    blueprint_id: str = Field(default_factory=lambda: _new_id("bp"))
    source_exam_doc_id: str | None = Field(default=None, min_length=3, max_length=128)
        # đổi thành str | None (duyệt 2026-10-26 chiều). Đề xuất đổi sang source_doc_ids: list[str] không bị bỏ,
        # chỉ để sau (quyết định (3) ngày 2026-10-26).
        # Validator: null chỉ khi BlueprintCreate.item_ids rỗng, tức blueprint không có slot question_kind =
        # "computational" (duyệt 2026-10-26 chiều). Nguồn khi có item (đề xuất (chi tiết), chờ duyệt): code chép
        # source_doc_id của item đầu tiên trong item_ids; blueprint chỉ có theory_slots thì null
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # BlueprintCreate.subject_code
    total_questions: int = Field(ge=1, le=200)   # code: = len(slots)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)   # BlueprintCreate.duration_minutes
    slots: list[BlueprintSlot]
    created_at: datetime
    # computed: chapter_distribution, format_distribution, cloneable_ratio, mean_mapping_confidence (code)
```

**Contract đã triển khai (duyệt 2026-10-28; `domain/schemas.py`):**

```python
class TheorySlot(StrictModel):                   # model riêng, thay BlueprintSlot.question_kind = "theory" (nợ ADR-004)
    slot_index: int = Field(ge=1)                # code: thứ tự theory_slots trong request, từ 1
    is_computational: Literal[False] = False     # code: khoá phân biệt với BlueprintSlot trong ExamBlueprint.slots
    question_kind: Literal["theory"] = "theory"  # code
    chapter_code: str = Field(pattern=CHAPTER_CODE_PATTERN)   # TheorySlotSpec.chapter_code (người dùng)
    source_chunk_id: str | None = Field(default=None, min_length=1, max_length=128)
        # TheorySlotSpec.source_chunk_id (người dùng). Field ngoài §5.9 cũ (nợ ADR-004): bản (a) mục tiêu không lưu chunk gợi ý trên slot
    format: QuestionFormat                       # TheorySlotSpec.format; null ⇒ code lấy từ Subject.exam_format (§5.14); chỉ MCQ4 hoặc ESSAY
    topic_key: str | None = None                 # code: luôn null với slot lý thuyết
    cognitive_level: CognitiveLevel = CognitiveLevel.REMEMBER   # TheorySlotSpec.cognitive_level; chỉ remember hoặc understand
    non_cloneable: Literal[False] = False        # code
    mapping_confidence: float = Field(default=1.0, ge=0.0, le=1.0)   # code: 1.0 (người dùng chọn chương)
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW          # code
    # KHÔNG có source_page_number, self_solve, distractor_archetypes (khác §5.9 mục tiêu)

ExamBlueprintSlot = Annotated[
    Annotated[BlueprintSlot, Tag("computational")] | Annotated[TheorySlot, Tag("theory")],
    Discriminator(_slot_kind),                   # question_kind == "theory" ⇒ "theory"; còn lại (thiếu field, hoặc slot cũ is_computational = false) ⇒ "computational" (BlueprintSlot)
]
# ExamBlueprint.slots: list[ExamBlueprintSlot]; các field còn lại như khối ExamBlueprint ở trên
```

- `BlueprintSlot` (đã triển khai) vẫn là slot tính toán với `is_computational: bool`, `source_page_number` bắt buộc, `topic_key` và `self_solve` bắt buộc; KHÔNG có `question_kind`. Khác §5.9 mục tiêu ở bốn điểm đều là nợ ADR-004 (xem mục "Nợ" tương ứng ở ADR-004): `question_kind` ở `BlueprintSlot`, field nullable, `TheorySlot` riêng, `source_chunk_id`.
- `ExamBlueprint.source_exam_doc_id: str | None` (duyệt 2026-10-26 chiều) và validator "null chỉ khi không có slot `BlueprintSlot`" đã có trong code. Blueprint dựng bởi `POST /v1/blueprints` (§6.7) luôn có `source_exam_doc_id = null`, vì chỉ nhận slot lý thuyết.
- `total_questions = len(slots)`; `slot_index` liên tục 1..N; các computed field (`chapter_distribution`, `format_distribution`, `cloneable_ratio`, `mean_mapping_confidence`) do code tính từ `slots`, đọc `chapter_code`, `format`, `non_cloneable`, `mapping_confidence` chung của hai kiểu slot.
- `BlueprintSummary {blueprint_id, subject_code, total_questions, created_at, batch_count}` (§6.7): `batch_count` do code đếm số batch twin đã lưu của blueprint.
- `BlueprintCreate` và `TheorySlotSpec` đã triển khai đúng như khối ở dưới (§5.9): `item_ids` ≤ 50 và không trùng, `theory_slots` ≤ 50, `item_ids` và `theory_slots` không cùng rỗng.

- **Chuyển `is_computational` sang `question_kind` (ADR-005 mục 1):** `is_computational=True` ⇔ `question_kind="computational"`, `False` ⇔ `"theory"`. Không có dữ liệu lưu lâu dài cần chuyển (store in-memory). `examples/exam_blueprint_sample.json` đổi field này khi triển khai; đây là thay đổi có chủ đích, duyệt qua `git diff examples/`.
- Validator mới: `computational` ⇒ có `topic_key` và `self_solve`, `format ∈ {MCQ4, NUMERICAL}`; `theory` ⇒ `format ∈ {MCQ4, ESSAY}` (`ESSAY` thêm ngày 2026-10-25, §5.14), `distractor_archetypes = []`.
- **`distractor_archetypes` của slot tính toán (đề xuất):** self-solve chạy `make_distractors()` trong sandbox trên `params` của item (đã có trong `SandboxResult.distractors`). Với mỗi trong 3 misconception của template, code so `value` của phương án nhiễu với số đầu tiên trong từng phương án sai của đề gốc, cùng dung sai như self-solve (§7.2 S5). Khớp đúng một phương án thì ghi `observed_option_key`/`observed_option_text` và `reproduction_confidence = 1.0`; không khớp thì để `null` và `0.0`. Code chỉ so sánh, không tính số (I1).

```python
class TheorySlotSpec(StrictModel):               # duyệt 2026-10-26 (§17 D8; lát cắt dọc ở §16 cần model này)
    chapter_code: str = Field(pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")   # người dùng
    cognitive_level: Literal["remember", "understand"] = "remember"        # người dùng
    source_chunk_id: str | None = None           # người dùng: chunk gợi ý slot, ví dụ một câu trong đề cương
    format: Literal["MCQ4", "ESSAY"] | None = None
        # người dùng chọn (field duyệt 2026-10-26, §5.14). Quy tắc khi null (đề xuất, chờ duyệt, §6.7 bước 5):
        # code lấy mặc định từ Subject.exam_format (mcq → MCQ4, essay → ESSAY, mixed → MCQ4).
        # Code không bao giờ đổi ESSAY thành MCQ4

class BlueprintCreate(StrictModel):              # thay bản v0.3 ở §5.6
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # người dùng; PHẢI khớp labels.subject_code của mọi item
    item_ids: list[str] = Field(default_factory=list, max_length=50)       # mọi item PHẢI approved, không non_cloneable (I5)
    theory_slots: list[TheorySlotSpec] = Field(default_factory=list, max_length=50)   # người dùng; duyệt 2026-10-26 (§17 D8)
    duration_minutes: int | None = Field(default=None, ge=5, le=300)
    # validator: item_ids không trùng; item_ids và theory_slots không cùng rỗng
```

**Đề cương làm nguồn blueprint (đã duyệt 2026-10-25).** Đề cương Vi xử lý là danh sách 56 câu hỏi không có đáp án, nên không phải đề thi (không qua Gate, không có phương án để self-solve). Cách làm: nhập đề cương như một tài liệu Vault với `source_kind = "study_guide"` (§6.5). Mỗi câu trong đề cương nằm trong một chunk; người dùng có thể chọn chunk đó làm `TheorySlotSpec.source_chunk_id`, và `chapter_code` của slot mặc định lấy từ chunk. Câu hỏi sinh ra vẫn đến từ cặp khái niệm đã xác nhận cùng chương (ADR-007); văn bản câu trong đề cương chỉ là gợi ý slot, không thành đáp án.

### 5.10 Template: `QuestionTemplate` và `input_schema` (ADR-005 mục 2, 5)

Phần đã có trong `domain/schemas.py` (chép nguyên văn):

```python
class TemplateVariable(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: Literal["int", "float", "list_int", "choice_int", "str"]
    min_value: float | None = None
    max_value: float | None = None
    length_min: int | None = Field(default=None, ge=1)
    length_max: int | None = Field(default=None, ge=1)
    choices: list[int] | None = None
    description: str = Field(min_length=3, max_length=300)

class TemplateAssertion(StrictModel):
    assert_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,47}$")
    purpose: AssertionPurpose                   # validity | tie_breaking | non_degeneracy | distinctness
    description: str = Field(min_length=5, max_length=300)   # thông điệp tiếng Việt khi assert VALIDITY_* lỗi (ADR-005 Hệ quả)

class AnswerSpec(StrictModel):
    unit: str | None = Field(default=None, max_length=16)
    decimals: int = Field(ge=0, le=6)
    tolerance_abs: float = Field(ge=0.0)
    min_option_gap: float = Field(gt=0.0)

class MisconceptionSpec(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)
    label_vi: str = Field(min_length=3, max_length=120)
    explanation_vi: str = Field(min_length=10, max_length=600)

class QuestionTemplate(StrictModel):
    template_id: str
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)
    version: int = Field(default=1, ge=1)
    runtime: Literal["python3.12"] = "python3.12"
    provenance: TemplateProvenance              # llm_generated | precomputed_verified
    generator_source: str = Field(min_length=50, max_length=20_000)
    entrypoints: tuple[str, ...]                # 5 hàm của §8.1
    solve_signature: Literal["def solve(params: dict) -> dict"]
    variables: list[TemplateVariable] = Field(min_length=1)
    assertions: list[TemplateAssertion] = Field(min_length=1)
    answer: AnswerSpec
    misconceptions: list[MisconceptionSpec] = Field(min_length=3, max_length=3)
    theory_chunk_ids: list[str] = []            # không còn dùng để tìm trích dẫn (ADR-006 mục 6); đề xuất: xoá ở ADR-005 bước 1
    created_at: datetime
    # computed: source_sha256 = sha256(generator_source) (code) — chính là template_sha256 của I4
    input_schema: InputSchema                   # đổi theo ADR-005 mục 2
    required_artifacts: list[Literal["timeline", "table"]] = []   # đổi theo ADR-005 mục 4
```

Phần thêm theo ADR-005 (tên `name`, `kind`, miền giá trị, `header_aliases` theo ADR; `max_rows` đã duyệt 2026-10-24; field còn lại là đề xuất):

```python
class InputColumn(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")   # khoá trong params; duy nhất trong cả input_schema
    kind: Literal["label", "int", "float"]
    min_value: float | None = None              # miền kiểm ở Gate S3 (OUT_OF_RANGE)
    max_value: float | None = None
    header_aliases: list[str] = Field(min_length=1)   # so sau chuẩn hoá trim, gộp khoảng trắng, lower (HEADER_MISMATCH)
    unique: bool = False                        # đề xuất: true ⇒ DUPLICATE_LABEL khi trùng (thay luật gắn cứng cho cột tên tiến trình)

class InputTable(StrictModel):
    columns: list[InputColumn] = Field(min_length=1)
    min_rows: int = Field(default=1, ge=1)      # đề xuất
    max_rows: int = Field(ge=1)                 # thay MAX_PROCESSES (đã duyệt 2026-10-24)

class InputScalar(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")   # khoá trong params, ví dụ "quantum"
    kind: Literal["int", "float"]
    min_value: float | None = None
    max_value: float | None = None
    label_vi: str = Field(min_length=1, max_length=80)     # đề xuất: nhãn cho form sinh từ input_schema (ADR-005 mục 6)

class InputSchema(StrictModel):
    tables: list[InputTable] = Field(default_factory=list, max_length=1)
        # đề xuất: tối đa 1 bảng trong MVP, vì IngestionItem chỉ có một table_primary
    scalars: list[InputScalar] = []
```

- **Chuyển bảng thành `params` (một hàm chung, ADR-005 mục 2):** mỗi cột thành `params[column.name]` là danh sách giá trị theo thứ tự dòng (`label` → `str`, `int` → `int`, `float` → `float`); mỗi tham số vô hướng thành `params[scalar.name]`. Luật riêng của dạng bài (RR cần `quantum`, SJF không có `quantum`) nằm trong `validate()` dạng `assert` có nhãn `VALIDITY_*`, chạy trong sandbox (ADR-005 mục 3).
- **Giữ hành vi I5 của `tests/test_input_limits.py`:** template RR khai báo `max_rows = 10`, các cột `names` (`unique = true`), `arrivals` (0–200), `bursts` (1–100) và scalar `quantum` (1–20). Bảng vượt `max_rows` sinh `OUT_OF_RANGE` (BLOCKING) và self-solve `SKIPPED` mà không thêm `NOT_A_NUMBER`, đúng như hiện nay.
- Metadata (`variables`, `assertions`, `answer`, `misconceptions`, `input_schema`, `required_artifacts`) nằm trong file `.json` đi kèm file `.py` trong `infrastructure/templates/` (ADR-005 mục 5).

### 5.11 Câu hỏi đầu ra: `TwinQuestion`, artifact, trace, `ReviewTicket`, `PatchAudit`

Đã có trong code (chép nguyên văn):

```python
class MisconceptionRef(StrictModel):
    misconception_code: str = Field(pattern=MISCONCEPTION_CODE_PATTERN)
    label_vi: str = Field(min_length=3, max_length=120)
    rationale_vi: str = Field(min_length=10, max_length=600)
    # đã duyệt 2026-10-25: citation: Citation | None = None — câu lý thuyết: trích dẫn định nghĩa của khái niệm ứng với
    # phương án nhiễu (ADR-007 mục 1; nguồn: code, Citation dựng từ cặp khái niệm đó, verbatim_quote = definition_quote);
    # null với câu tính toán

class NumericalAnswer(StrictModel):              # sandbox: solve()["value"], AnswerSpec
    value: float
    tolerance_abs: float = Field(ge=0.0)
    unit: str | None = None
    decimals: int = Field(ge=0, le=6)

class GenerationTrace(StrictModel):              # code hiện tại; chỉ cho câu tính toán
    sandbox_duration_ms: int = Field(ge=0)
    llm_attempts: int = Field(ge=0, le=3)
    seed_rerolls: int = Field(ge=0)
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: TemplateProvenance

# services/pipeline.py
class AttemptLog(BaseModel):
    attempt: int
    provenance: TemplateProvenance
    template_sha256: str | None
    outcome: str
    seeds_tried: int
    assertion_histogram: dict[str, int] = {}
    feedback_sent: list[str] = []

class ReviewTicket(BaseModel):
    blueprint_id: str
    slot_index: int
    topic_key: str                               # đổi thành str | None, null với slot lý thuyết (duyệt 2026-10-26)
    reason: str                                  # tiếng Việt, do code ghi
    attempts: list[AttemptLog]

# services/ingestion_gate.py
class PatchAudit(BaseModel):
    table_id: str
    from_version: int
    to_version: int
    editor_id: str
    changes: list[dict[str, Any]]                # [{row, col, old, new}]
    at: datetime
```

- Đề xuất: `PatchAudit.changes` thành `list[PatchChange]` với `PatchChange {row: int, col: int, old: str | None, new: str}`, để đúng CLAUDE.md §4.1 (không dùng `dict` trần qua ranh giới). JSON không đổi; `types/holocron.ts` đã có `PatchChange`.
- `ReviewTicket`, `AttemptLog`, `PatchAudit`, `TwinBatch` và `IngestionItem` hiện nằm ở `services/` và `routers/`. Repository SQLite ở `infrastructure/` (§5.13) không được import hai tầng đó (CLAUDE.md §2.2), nên các model này PHẢI chuyển về `domain/` trước.

**Artifact lời giải (đổi theo ADR-005 mục 4, bước 3):**

```python
class TimelineSegment(StrictModel):              # sandbox
    label: str = Field(min_length=1, max_length=32)
    start: int = Field(ge=0)
    end: int                                     # > start

class TimelineArtifact(StrictModel):
    kind: Literal["timeline"]
    caption_vi: str | None = Field(default=None, max_length=120)   # đề xuất
    segments: list[TimelineSegment] = Field(min_length=1)          # sắp theo start, không chồng lấn

class TableArtifact(StrictModel):
    kind: Literal["table"]
    caption_vi: str | None = Field(default=None, max_length=120)   # đề xuất: phân biệt nhiều bảng (thanh ghi, CPSR, ô nhớ)
    headers: list[str] = Field(min_length=1)
    rows: list[list[str]]                        # chuỗi hiển thị do sandbox sinh; mỗi dòng đúng len(headers) ô

SolutionArtifact = Annotated[TimelineArtifact | TableArtifact, Field(discriminator="kind")]
```

- Nguồn: khoá `"artifacts"` của `solve()` (§8.1), kiểm kiểu theo `kind` trong sandbox (thay `_gantt_ok`). `build_twin` kiểm template trả đủ các `kind` trong `required_artifacts`. Frontend chọn component theo `kind`, không tự tính (I1).

**`TwinQuestion` sau ADR-005 và ADR-007:**

```python
class TwinQuestion(StrictModel):
    # giữ nguyên: twin_id, blueprint_id, slot_index, seed, format, stem, options, correct_key,
    #             numerical_answer, distractor_to_misconception_map, solution_explanation_vi,
    #             citation, review_status, generated_at
    question_kind: Literal["computational", "theory"]   # ADR-005 mục 1
    template_id: str | None                      # đã duyệt 2026-10-25: null với câu lý thuyết; câu tính toán: template đã chạy
    topic_key: str | None                        # đã duyệt 2026-10-25: null với câu lý thuyết; câu tính toán: slot.topic_key
    artifacts: list[SolutionArtifact] = []       # ADR-005 bước 3, thay gantt và validator rẽ nhánh theo topic_key
    trace: GenerationTrace                       # union theo kind (đã duyệt 2026-10-25), xem dưới
    essay: EssaySpec | None = None               # code; bắt buộc ⇔ format = ESSAY (§5.14; field đã duyệt 2026-10-25, chi tiết đề xuất)
```

- Câu lý thuyết MCQ4 (ADR-007): `artifacts = []`; `stem`, `options`, `solution_explanation_vi` do code ghép từ `term` và `definition_quote` của các cặp đã xác nhận, LLM không soạn; `citation` là trích dẫn của cặp đúng; mỗi phương án nhiễu mang mã thuộc `THEORY_MISCONCEPTIONS` (I2). Đề xuất: `solution_explanation_vi` là định nghĩa nguyên văn của cặp đúng.
- **Dạng câu lý thuyết MCQ4 (duyệt 2026-10-26):** dạng câu (ghép khái niệm, điền khuyết, chọn định nghĩa đúng; ADR-007 mục 1) do chính RNG tất định `random.Random(f"{generator_version}:{pool_sha256}:{seed}")` chọn trong các dạng khả thi của pool, nên bộ ba I4 hiện có `(generator_version, pool_sha256, seed)` đủ để tái lập câu, kể cả dạng câu; không thêm field nào vào `TheoryTrace`. Đổi luật chọn dạng thì tăng `generator_version` (ADR-007 mục 2). Đề xuất, chờ duyệt: (a) thứ tự rút là dạng trước, cặp sau; (b) danh sách dạng khả thi giữ thứ tự khai báo cố định (ghép khái niệm, điền khuyết, chọn định nghĩa đúng), một dạng là khả thi khi pool có ít nhất một cặp sinh được câu dạng đó theo điều kiện ở ADR-007 mục 1; (c) không dạng nào khả thi thì slot vào `ReviewTicket` với lý do "không đủ căn cứ trong Vault". Câu `ESSAY` chỉ có một dạng (§5.14), không qua bước chọn dạng.
- **Luật "đúng một đáp án" của câu lý thuyết MCQ4 (ADR-007 mục 1):** bộ sinh loại khỏi phương án nhiễu mọi cặp mà phương án của nó cũng là một đáp án đúng (trích dẫn lồng với trích dẫn đáp án, hoặc phép điền thử vào chỗ `____` khớp một trích dẫn của nhóm ứng viên, như mô tả ở ADR-007 mục 1).
  - **Đã duyệt hướng (2026-10-26, quyết định (1)):** so xung đột trên dãy token chữ/số; xét theo khái niệm (mọi cặp của khái niệm); áp ở cả ba dạng (ghép khái niệm, điền khuyết, chọn định nghĩa đúng); đổi luật thì tăng `GENERATOR_VERSION` thành `"theory_mcq4_v3"`. Mỗi ca đã lọt ở v2 (ngoặc kép bao thuật ngữ, gạch nối khác loại, ký hiệu đầu dòng, soft hyphen, dạng chọn định nghĩa chỉ so một cặp neo) có test riêng; nhánh triển khai báo tỉ lệ slot thành `ReviewTicket` trước và sau khi đổi luật.
  - **Chi tiết: chờ duyệt, xem câu 1b** (danh sách câu hỏi chờ người dùng). Gồm: luật token thay luật ký tự của v2 hay cộng thêm vào; xử lý ký tự định dạng (Unicode `Cf`); xử lý dãy token rỗng; khoá dùng để xác định "cùng khái niệm". Nhánh triển khai không chốt các điểm này trước khi câu 1b được trả lời.
  - **Gói khuyến nghị của câu 1b (đề xuất, chờ duyệt):** (a) xung đột khi luật token HOẶC luật ký tự của v2 báo xung đột (chặt hơn v2, giữ mọi ca v2 đã bắt); (b) khoá khái niệm giữ `_option_key` của code hiện tại, để "C" khác "C#"; (c) token là dãy liên tiếp các ký tự thuộc nhóm Unicode `L*`, `N*`, `M*`; (d) soft hyphen U+00AD và U+200C, U+200D, U+2060, U+FEFF bị bỏ mà không tách token; U+200B và mọi ký tự khác tách token; (e) dãy token rỗng (thuật ngữ chỉ có ký hiệu) thì chỉ dùng luật ký tự; (f) cho phép đổi đích danh các test ghim v2 ở `tests/test_theory_generator.py`. Lý do không so token đơn thuần: trích dẫn bôi lẹm giữa từ (ví dụ "ông gian…") vẫn qua I3, và chữ không có khoảng cách giữa các từ, so token đơn thuần bắt kém hơn luật ký tự v2 ở các ca này.
  - Luật chỉ đọc dữ liệu đã nằm trong `pool_sha256` (I4), như v2. Đổi luật làm đổi câu sinh ra với cùng `pool_sha256` và `seed`, nên tăng `generator_version` (ADR-007 mục 2); `examples/` sinh câu lý thuyết đổi theo, duyệt qua `git diff examples/`. Code hiện tại vẫn là `theory_mcq4_v2` cho tới khi nhánh triển khai luật mới được merge. Lưu ý cho nhánh triển khai: `tests/test_theory_generator.py` (file cưỡng chế I2 và I4, CLAUDE.md §9) có assertion ghim chuỗi `"theory_mcq4_v2"` và ghim luật v2; đổi các assertion đó là sửa test sẵn có, cần người dùng đồng ý riêng trước khi làm (CLAUDE.md §7); các test I2/I4 khác của file PHẢI xanh mà không sửa.
- Câu lý thuyết `ESSAY`: quy tắc riêng ở §5.14.
- **Trace (đã duyệt 2026-10-25; ADR-007 Hệ quả để mở contract này):**

```python
class ComputationalTrace(StrictModel):
    kind: Literal["computational"]
    sandbox_duration_ms: int = Field(ge=0)
    llm_attempts: int = Field(ge=0, le=3)
    seed_rerolls: int = Field(ge=0)
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: TemplateProvenance

class TheoryTrace(StrictModel):
    kind: Literal["theory"]
    generator_version: str = Field(min_length=1, max_length=32)
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")   # cách tính ở ADR-007 mục 2
    seed_rerolls: int = Field(ge=0)

GenerationTrace = Annotated[ComputationalTrace | TheoryTrace, Field(discriminator="kind")]
```

  Hệ quả: `trace` trong `examples/*.json` có thêm `"kind"`; duyệt qua `git diff examples/`.

- **Nối câu lý thuyết vào luồng twin (duyệt 2026-10-25):** `TwinQuestion.template_id` và `topic_key` cho phép `null` (câu lý thuyết); `trace` của câu lý thuyết là `TheoryTrace`; `MisconceptionRef.citation`; `BlueprintSlot.question_kind` thay `is_computational` (§5.9). Walkthrough được sinh thêm ví dụ câu lý thuyết trong `examples/`, cũng phải giống từng byte (I4).

**Contract đã triển khai cho câu lý thuyết trong `TwinBatch` (duyệt 2026-10-28, quyết định 14(b)).** Các khối `TwinQuestion` (có `question_kind`, `template_id`/`topic_key` nullable, `trace` union) và `TheoryTrace` ở trên là **mục tiêu sau 29/10 (nợ ADR-004)**; chuyển sang phương án (a) làm đổi model, pipeline và `examples/`. Bản đã triển khai (`domain/twins.py`, `domain/theory.py`) không đổi `TwinQuestion` và `GenerationTrace`:

```python
class TheoryQuestion(StrictModel):               # domain/theory.py; không id, không thời điểm (I4)
    question_kind: Literal["theory"] = "theory"
    form: TheoryQuestionForm                     # code: RNG của (generator_version, pool_sha256, seed) chọn dạng (xem trên)
    subject_code: str                            # slot/blueprint
    chapter_code: str                            # TheorySlot.chapter_code
    seed: int = Field(ge=0, lt=SEED_SPACE)       # code: theory_seed(base_seed, k, j), §6.3
    generator_version: str                       # code (hằng của bộ sinh)
    pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")   # code: ADR-007 mục 2
    stem: str; options: dict[OptionKey, str]; correct_key: OptionKey   # code ghép từ term và definition_quote của cặp khái niệm confirmed
    distractor_to_misconception_map: dict[OptionKey, MisconceptionRef]  # mã thuộc THEORY_MISCONCEPTIONS (I2)
    distractor_citations: dict[OptionKey, Citation]                     # code: definition_quote của khái niệm ứng với phương án nhiễu (I3)
    solution_explanation_vi: str
    citation: Citation                           # trích dẫn nguyên văn của cặp đúng (I3)

class TheoryTwin(StrictModel):                   # domain/twins.py
    question_kind: Literal["theory"] = "theory"  # khoá phân biệt
    twin_id: str                                 # code: "twin_" + id sinh khi tạo
    blueprint_id: str                            # blueprint
    slot_index: int = Field(ge=1)                # TheorySlot.slot_index
    format: Literal["MCQ4"] = "MCQ4"             # code; slot ESSAY chưa sinh được (vào review_tickets, §6.9)
    question: TheoryQuestion                     # bọc câu; bộ ba I4 (generator_version, pool_sha256, seed) nằm trong question
    review_status: ReviewStatus                  # code: AUTO_PASS (duyệt 2026-10-28, câu 24)
    generated_at: datetime                       # code

BatchTwin = Annotated[
    Annotated[TwinQuestion, Tag("computational")] | Annotated[TheoryTwin, Tag("theory")],
    Discriminator(_twin_kind),                   # question_kind == "theory" ⇒ "theory"; còn lại ⇒ "computational" (TwinQuestion không có question_kind)
]

class TwinBatch(BaseModel):
    batch_id: str = Field(pattern=BATCH_ID_PATTERN)   # code (đã duyệt 2026-10-25, §6.7)
    blueprint_id: str
    base_seed: int                               # TwinBatchRequest.base_seed hoặc mặc định theo blueprint_id (§6.3)
    twins: list[BatchTwin]                       # union theo question_kind; thay list[TwinQuestion]
    review_tickets: list[ReviewTicket]
    created_at: datetime                         # code: đồng hồ của request
```

- `ReviewTicket.topic_key: str | None` đã có trong code: null với slot lý thuyết (duyệt 2026-10-26, nay đã triển khai). Câu lý thuyết thiếu căn cứ trong Vault, slot `ESSAY` chưa sinh được, hay hết dải seed mà vẫn trùng ⇒ một `ReviewTicket` mỗi slot, `attempts = []`. Hai trường hợp Vault không nhất quán cũng thành `ReviewTicket` (theo code, PR B5a+F4, 2026-10-28; người dùng chưa duyệt riêng, là sửa lỗi 500 theo review, shape không đổi): cặp khái niệm trong pool ứng viên của môn mà chunk nguồn không còn ⇒ lý do `THEORY_MISSING_CHUNK_REASON`, không sinh twin; trích dẫn không còn khớp nguyên văn chunk (`QuoteMismatchError`) ⇒ lý do `THEORY_QUOTE_MISMATCH_REASON`, giữ các twin đã sinh của slot (chi tiết §6.3).
- **Khác contract mục tiêu (nợ ADR-004):** không có `TheoryTrace` (bộ ba I4 nằm trong `TheoryQuestion`, không phải `trace`); `TheoryTwin` bọc `TheoryQuestion` thay vì `TwinQuestion` mang `question_kind = "theory"`; `TheoryTwin` không có `numerical_answer`, `artifacts`, `essay`, `template_id`, `topic_key`.
- `review_status` của `TheoryTwin` là `AUTO_PASS`: duyệt 2026-10-28 (câu 24) (spec chưa quy định giá trị này cho câu lý thuyết; slot lý thuyết mặc định `NEEDS_REVIEW` ở `TheorySlot` là field khác, của slot).
- Frontend chọn component theo `question_kind` của phần tử trong `twins`; `types/holocron.ts` khớp 1:1 (§2.3 CLAUDE.md).

### 5.12 `RuntimeInfo` (F17; định nghĩa `demo_mode` đã duyệt 2026-10-23; chế độ khả năng đã duyệt 2026-10-25, §11.4)

```python
CapabilityName = Literal[
    "ocr",                          # tầng 2 của bộ nhập: Tesseract + vie.traineddata đủ chữ (ADR-008 mục 8, mục 14 test 1)
    "ocr_cross_check",              # lượt đọc thứ hai RapidOCR (ADR-008 mục 11)
    "vault_search",                 # tìm BM25 trong Vault (ADR-006 mục 4; FTS5 hoặc BM25 tự cài)
    "theory_generation",            # sinh câu lý thuyết tất định từ cặp đã xác nhận (ADR-007)
    "concept_pair_llm_proposal",    # LLM đề xuất cặp khái niệm (§6.6)
    "essay_grading_offline",        # chấm tự luận bằng n-gram (§5.14)
    "essay_grading_llm",            # AnswerJudge (ADR-007 mục 5; sau MVP)
    "template_authoring_llm",       # TemplateAuthor viết template ứng viên (§10, §11.3)
    "vlm",                          # tầng 4 và dual-pass VLM (§11.2; đang park)
    # Ba tên dưới (đề xuất, chờ duyệt (2026-10-29); nguồn yêu cầu: design-handoff/README.md mục 9): khả năng chờ backend của UI mới,
    # mặc định available = false, reason = "not_implemented"; ADR-004 mục "Khả năng chờ backend (UI mới)"
    "concept_auto_proposal",        # đề xuất khái niệm tự động, không cần LLM: thuật ngữ + trích dẫn nguyên văn + số trang (đề xuất, chờ duyệt (2026-10-29))
    "line_review_queue",            # hàng đợi duyệt theo dòng nghi ngờ; khi làm thật cần ADR I5 riêng (đề xuất, chờ duyệt (2026-10-29))
    "page_progress_eta",            # tiến độ theo trang và thời gian còn lại cho từng tài liệu đang đọc (đề xuất, chờ duyệt (2026-10-29))
]
CapabilityReason = Literal[        # danh sách mã lý do: duyệt 2026-10-26 (§17 D7); khái niệm "khả năng kèm lý do" đã duyệt 2026-10-25
    "llm_not_configured",           # thiếu HOLOCRON_LLM_BASE_URL, HOLOCRON_LLM_API_KEY hoặc HOLOCRON_LLM_MODEL_EXTRACT (§11.2)
    "vlm_not_configured",           # thiếu một trong ba biến HOLOCRON_VLM_*
    "ocr_engine_missing",           # không chạy được binary Tesseract
    "ocr_language_missing",         # thiếu vie.traineddata hoặc unicharset thiếu chữ tiếng Việt ngoài OCR_CHARSET_ALLOWED_MISSING (ADR-008 mục 14)
    "secondary_ocr_disabled",       # CHỈ khi HOLOCRON_PDF_OCR_SECONDARY để trống
    "not_implemented",              # code của khả năng này chưa có trong bản đang chạy
    "secondary_ocr_unavailable",    # (duyệt 2026-10-29) muốn bật lượt đọc thứ hai nhưng không nạp được RapidOCR (bản rút gọn không kèm, thiếu gói, model hỏng); kiểm trong tiến trình con có giới hạn lúc khởi động
    "local_check_pending",          # (duyệt 2026-10-30) kiểm cục bộ nền (Tesseract, unicharset, RapidOCR) chưa xong sau khi server mở cổng; CHỈ áp cho `ocr` và `ocr_cross_check`; thêm CUỐI danh sách, sau secondary_ocr_unavailable
]
# detail_vi của secondary_ocr_unavailable (duyệt 2026-10-29, nguyên văn): "Không nạp được lượt đọc thứ hai (RapidOCR) trên máy này.
# Bản rút gọn không kèm RapidOCR: dùng bản đầy đủ. Nếu đang dùng bản đầy đủ, cài lại ứng dụng."
# detail_vi của local_check_pending (duyệt 2026-10-30, nguyên văn): "Holocron đang kiểm tra OCR trên máy này. Đợi vài giây rồi xem lại."
# Thứ tự trong danh sách CapabilityReason: secondary_ocr_unavailable đứng cuối, trước local_check_pending (khớp domain/capabilities.py).
# Kiểm nền sau khi mở cổng (duyệt 2026-10-30): lifespan KHÔNG chờ kiểm. Server mở cổng ngay; một luồng/tác vụ nền chạy kiểm một lần
# (Tesseract, unicharset, RapidOCR trong tiến trình con) rồi lưu kết quả bất biến vào app.state. Trong lúc chưa xong, `ocr` và
# `ocr_cross_check` có available = false, reason = "local_check_pending". Trạng thái pending chỉ để hiển thị: bộ nhập tài liệu
# KHÔNG bị chặn và vẫn chạy như cũ. Luồng nền lỗi ⇒ ghi log và coi như ocr_engine_missing (không treo pending mãi).
# Kiểm khả năng `ocr` (duyệt 2026-10-30; thay dòng duyệt 2026-10-29): chỉ kiểm `vie.traineddata` ở ĐÚNG thư mục mà lần gọi OCR
# thật sẽ dùng: (1) HOLOCRON_TESSDATA_DIR đặt ⇒ app truyền `--tessdata-dir` và kiểm thư mục đó; (2) không đặt, Tesseract 5.x ⇒
# kiểm thư mục do `--list-langs` in ra; (3) không đặt, Tesseract 4.x (không in thư mục) ⇒ đường lùi HOLOCRON_TESSDATA_DIR →
# đường dẫn mặc định của bản cài (danh sách hằng trong config). Vì HOLOCRON_TESSDATA_DIR khi đặt luôn được truyền bằng
# `--tessdata-dir`, đường lùi 4.x thực tế chỉ còn thư mục mặc định của bản cài (và cạnh tesseract.exe trên Windows).
# TESSDATA_PREFIX KHÔNG nằm trong thứ tự dò và app không truyền nó cho Tesseract (worker chạy môi trường tối thiểu). Một hàm
# chung xác định thư mục tessdata, dùng cho cả kiểm khởi động lẫn worker. Hai trường hợp phân biệt (duyệt 2026-10-30):
# (1) thư mục ĐÃ xác định (`--tessdata-dir` từ HOLOCRON_TESSDATA_DIR, hoặc thư mục `--list-langs` in ra) mà KHÔNG TỒN TẠI,
# thiếu `vie.traineddata`, hoặc file không phải file thường ⇒ `ocr_language_missing`; `detail_vi` nêu tên biến
# HOLOCRON_TESSDATA_DIR, không nêu đường dẫn; có file ⇒ kiểm unicharset bình thường. (2) KHÔNG xác định được thư mục
# (Tesseract 4.x, đường lùi qua đường dẫn mặc định của bản cài không thấy) ⇒ bỏ qua kiểm unicharset, cảnh báo một lần, chỉ
# kiểm `vie` có trong danh sách ngôn ngữ (ngoại lệ đã duyệt 2026-10-30, ADR-008 mục 14). Không ghi đường dẫn tuyệt đối vào log.

class Capability(StrictModel):
    name: CapabilityName            # code
    available: bool                 # code: kiểm lúc khởi động (cấu hình, binary, unicharset); không gọi mạng
    reason: CapabilityReason | None # code; null ⇔ available = true
    detail_vi: str | None           # code: câu cố định theo reason, ví dụ hướng dẫn cài Tesseract; KHÔNG chứa giá trị biến,
                                    # key, tên model hay địa chỉ endpoint

class RuntimeInfo(BaseModel):       # routers/; không lộ key, tên model hay endpoint
    demo_mode: bool         # đổi: = not llm_configured ("thiếu key LLM", đã duyệt 2026-10-23). Code hiện: extractor đang chạy là Mock
    llm_configured: bool    # code: đủ ba biến LLM bắt buộc ở §11.2 (đã duyệt 2026-10-25; code hiện chỉ cần HOLOCRON_LLM_API_KEY)
    vlm_configured: bool    # code: đủ ba biến HOLOCRON_VLM_*
    mock_extractor: bool    # code (đề xuất, chờ duyệt; §17 D8): upload đề (§6.1) đang dùng MockDualPassExtractor, tức dữ liệu mẫu, không đọc file thật
    capabilities: list[Capability]   # code: đủ 12 phần tử (9 tên duyệt 2026-10-25 + 3 tên đề xuất, chờ duyệt (2026-10-29)), theo thứ tự khai báo của CapabilityName; ba tên mới luôn available = false, reason = "not_implemented" với mọi cấu hình, vì không nằm trong IMPLEMENTED_CAPABILITIES, LLM_CAPABILITIES, VLM_CAPABILITIES
    # Hai field dưới: hướng duyệt 2026-10-26 chiều; tên, kiểu và nguồn là đề xuất (chi tiết), chờ duyệt
    storage_mode: Literal["sqlite", "memory"]
        # code: theo kho thực sự đang chạy (kho SQLite ⇒ "sqlite", kho in-memory ⇒ "memory"), không chỉ theo việc
        # biến HOLOCRON_DATA_DIR có được đặt hay không (§5.13). KHÔNG chứa đường dẫn thư mục dữ liệu
    llm_data_recipient: str | None = Field(default=None, min_length=1, max_length=80)
        # người dùng: nhãn bên nhận dữ liệu gửi LLM, đọc từ biến môi trường mới HOLOCRON_LLM_DATA_RECIPIENT (§11.2),
        # ví dụ "Gateway của ban tổ chức hackathon". null khi biến chưa đặt, khi llm_configured = false (không dữ liệu
        # nào rời máy, G6), hoặc khi giá trị bị loại theo quy tắc dưới đây
    # Ba field dưới: duyệt 2026-10-28 (quyết định người dùng câu 7); đã chốt tên, kiểu và nguồn; đã triển khai (nhánh feat/api-runtime-build-info, duyệt 2026-10-28)
    app_version: str
        # code: đọc `version` từ file build của gói (§14); thiếu hoặc hỏng ⇒ version của gói đã cài, không có thì "dev"
    commit: str
        # code: đọc `commit` từ file build của gói (§14); thiếu hoặc hỏng ⇒ "unknown"
    log_dir: str | None
        # code: đọc biến môi trường HOLOCRON_LOG_DIR do launcher quản lý (§11.2, §14); null khi biến không được đặt (không chạy qua launcher hoặc launcher không mở được file log)
```

- **`storage_mode` và `llm_data_recipient` (hướng duyệt 2026-10-26 chiều; chi tiết là đề xuất (chi tiết), chờ duyệt):**
  - Quy tắc "không lộ key, tên model hay endpoint" của `RuntimeInfo` giữ nguyên. `llm_data_recipient` là nhãn do người dùng tự đặt, không suy ra từ `HOLOCRON_LLM_BASE_URL` hay tên model.
  - Code chuẩn hoá giá trị biến (bỏ khoảng trắng hai đầu; rỗng ⇒ chưa đặt, như §11.2) rồi loại (coi như chưa đặt, trả `null`, ghi cảnh báo chỉ nêu tên biến) khi giá trị: dài hơn 80 ký tự; chứa ký tự điều khiển; chứa `://`; hoặc chứa, không phân biệt hoa thường, giá trị của `HOLOCRON_LLM_API_KEY`, `HOLOCRON_LLM_MODEL_EXTRACT`, `HOLOCRON_LLM_MODEL_AUTHOR` hay tên máy (host) của `HOLOCRON_LLM_BASE_URL`. Phép so "chứa" chỉ dùng các giá trị khác rỗng sau chuẩn hoá: biến chưa đặt hoặc rỗng (ví dụ `HOLOCRON_LLM_MODEL_AUTHOR` không đặt), hay URL không tách được tên máy, thì bỏ qua giá trị đó, vì chuỗi rỗng là chuỗi con của mọi chuỗi và sẽ làm mọi nhãn bị loại. Nhờ vậy response không bao giờ lặp lại key, tên model hay địa chỉ endpoint, kể cả khi người dùng gõ nhầm.
  - Đọc một lần lúc khởi động cùng cấu hình LLM (§11.4), không gọi mạng.
  - Frontend: `storage_mode = "memory"` ⇒ thông báo ở màn chạy lần đầu (`/`) và trang nhập tài liệu rằng dữ liệu mất khi tắt app (đặt `HOLOCRON_DATA_DIR` để lưu lại); màn hình cấu hình và mọi nút gửi dữ liệu tới LLM hiện `llm_data_recipient`, hoặc câu cố định "endpoint LLM đã cấu hình (chưa đặt tên bên nhận)" khi `null` và `llm_configured = true` (§13.1).
  - `types/holocron.ts` cập nhật cùng PR thêm hai field (CLAUDE.md §2.3); test ở `tests/test_runtime_capabilities.py` (§15) thêm trường hợp giá trị bị loại.
- **`app_version`, `commit`, `log_dir` (duyệt 2026-10-28):** `app_version` và `commit` đến từ file build của gói (§14); khi file thiếu hoặc hỏng, `app_version` lùi về version của gói đã cài (không có thì "dev") và `commit` là "unknown"; không bao giờ làm lỗi endpoint. `log_dir` là đường dẫn thư mục log launcher đang ghi (§14), `null` khi không chạy qua launcher. Ba field không chứa key, tên model hay endpoint nên không vi phạm quy tắc của `RuntimeInfo`; riêng `log_dir` là đường dẫn máy cục bộ (lưu ý khi hiển thị). `types/holocron.ts` cập nhật cùng PR thêm ba field (CLAUDE.md §2.3).
- **Bỏ `ocr_available`** (đề xuất v0.5, chưa có trong code): thông tin này nay là phần tử `name = "ocr"` của `capabilities` (đề xuất, chờ duyệt).
- Một khả năng có thể `available = true` mà vẫn chạy ở mức thấp hơn, ví dụ `vault_search` dùng BM25 tự cài khi SQLite thiếu FTS5 (§5.13). Mức này không phải lý do không khả dụng nên không có `reason`.
- Ma trận khả năng ↔ điều kiện ↔ endpoint bị ảnh hưởng ở §11.4.

Frontend PHẢI hiển thị:
- Mỗi khả năng `available = false`: thông báo ở đúng màn hình dùng khả năng đó, kèm `detail_vi`; nút gọi endpoint cần khả năng đó bị vô hiệu. Không chặn các màn hình khác (G6).
- `demo_mode = true`: băng thông báo trên mọi trang, nói rõ chưa cấu hình LLM, nên nhãn (môn, chương, mức nhận thức) và cặp khái niệm phải nhập tay, và câu tính toán chỉ dùng template đã kiểm chứng. Đề xuất (chờ duyệt, §17 D10): đổi chữ "chế độ demo" trên UI thành "chạy offline, không có LLM", vì từ 2026-10-25 thiếu LLM là cách dùng bình thường chứ không phải bản demo.
- `mock_extractor = true` (đề xuất, chờ duyệt; §17 D8): nhãn ở trang tải đề và trang item, nói rõ bảng và phương án là dữ liệu mẫu, không đọc từ file vừa tải.
- Khả năng `ocr` có `available = false`: cảnh báo ở trang nhập tài liệu rằng trang scan sẽ `failed` (VLM đang park), kèm hướng dẫn cài Tesseract và `vie.traineddata`.

### 5.13 Lưu trữ SQLite (F14; BE-003)

Toàn bộ mục này đã duyệt 2026-10-25, trừ bảng `subjects` (đề xuất, chờ duyệt, thêm ở v0.6) và cột `documents.chapter_code` (thêm 2026-10-26; tên cột và cách lưu là đề xuất, chờ duyệt). Mục tiêu: thay `IngestionStore` in-memory mà không thêm dependency.

- **Thư viện:** `sqlite3` của thư viện chuẩn, không SQLAlchemy (D5, đã duyệt 2026-10-25; comment TODO trong `routers/ingestion.py` (trước khi tách router ở PR #37: `routers/api.py`) vẫn ghi SQLAlchemy và sửa cùng nhánh SQLite, §17 D5). Mỗi kết nối bật `PRAGMA foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 5000`; ghi trong `BEGIN IMMEDIATE`.
- **Vị trí file:** `HOLOCRON_DATA_DIR/holocron.sqlite3`; file gốc giữ ở `HOLOCRON_DATA_DIR/files/<sha256>.pdf` (ADR-006 mục 8). **Không đặt `HOLOCRON_DATA_DIR` thì backend dùng kho in-memory** (duyệt 2026-10-25; thay mặc định `%LOCALAPPDATA%\Holocron` / `$XDG_DATA_HOME/holocron` của v0.6): không có file SQLite, dữ liệu mất khi tắt app. Riêng file PDF gốc của bộ nhập (§6.5) vẫn được ghi tạm ra đĩa, vì worker đọc PDF chỉ mở được file trên đĩa (CLAUDE.md §2.2, ADR-008 mục 3) (duyệt 2026-10-26 chiều; thay câu "không ghi gì ra đĩa" của bản 2026-10-25):
  - File nằm trong một thư mục tạm riêng của tiến trình, đặt trong thư mục tạm của hệ điều hành, tên có tiền tố theo pid (`holocron_files_p<pid>_*`, `infrastructure/storage/original_files.py`).
  - Thư mục đó bị xoá khi tắt app.
  - Lần khởi động sau xoá các thư mục `holocron_files_p<pid>_*` còn sót của tiến trình đã chết (tắt đột ngột, hoặc xoá lỗi trên Windows); không đụng thư mục của tiến trình còn sống.

  Bản đóng gói (zip MVP-1, sau này Tauri) luôn đặt biến này qua launcher, bằng thư mục dữ liệu app (§1.5). Test dùng `:memory:` hoặc thư mục tạm.
- **Migration:** bảng `schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`; script SQL thuần `NNNN_<tên>.sql` trong `infrastructure/storage/migrations/` (khai báo package-data). Khi khởi động, chạy lần lượt các script có số lớn hơn version hiện tại, mỗi script trong một giao dịch kèm dòng `schema_version`. Không hỗ trợ hạ version: DB mới hơn code thì từ chối khởi động với thông điệp rõ.
- **Optimistic locking (BE-003):** mọi bảng sửa được có cột `version INTEGER NOT NULL DEFAULT 1`. Ghi bằng `UPDATE … SET …, version = version + 1 WHERE <khoá> = ? AND version = ?`; `rowcount = 0` mà bản ghi vẫn tồn tại ⇒ 409. Với `ingestion_items`, `version` bằng `table_primary.version` (§6.2).
- **Interface:** `IngestionStore` được thay bằng các repository sau `Protocol` (CLAUDE.md §2.2), cấp qua `Depends`: `DocumentRepository`, `VaultRepository`, `IngestionRepository`, `BlueprintRepository`, `TwinBatchRepository`, `TemplateRepository`, và `SubjectRepository` (đề xuất, cho bảng `subjects`). Bản in-memory giữ lại cho test và dùng khi không đặt `HOLOCRON_DATA_DIR` (duyệt 2026-10-25); bản SQLite ở `infrastructure/storage/`.

| Bảng | Khoá chính | Cột chính | Quan hệ | Index |
|---|---|---|---|---|
| `subjects` (đã triển khai, duyệt 2026-10-28; §5.14; migration `0003_subjects.sql`) | `subject_code` | `display_name`, `exam_format` (`mcq` \| `essay` \| `mixed`, CHECK), `created_at`, `updated_at`, `version` (≥ 1, mặc định 1) | Các bảng khác giữ `subject_code` dạng chuỗi, không khoá ngoại tới bảng này, để nhập tài liệu không phụ thuộc thứ tự tạo môn. Migration điền sẵn mỗi `subject_code` đã có ở `documents`, `chunks`, `concept_pairs`, `blueprints` thành một môn tạo ngầm (`display_name = subject_code`, `exam_format = mixed`) | — |
| `documents` | `document_id` (`doc_` + sha256[:16]) | `sha256` UNIQUE, `filename`, `media_type`, `size_bytes`, `subject_code` (null với upload đề §6.1), `chapter_code` (null với upload đề §6.1; lưu `chapter_code` của tài liệu nhập đã duyệt 2026-10-26, tên cột và việc lưu thành cột riêng của bảng này là đề xuất, chờ duyệt), `title`, `source_kind`, `output_language`, `page_count`, `has_text_layer`, `status`, `uploaded_at`, `version`; thêm 2026-10-31 (GĐ1, migration `0004_document_chapter_title.sql`): `chapter_title` (TEXT, null với bản ghi cũ), `chapter_code_source` (TEXT CHECK `user`\|`file_name`\|`next_free`, bản ghi cũ có `chapter_code` nhận `user`), `chapter_title_source` (TEXT CHECK `user`\|`repeated_header`\|`file_name`\|`llm`, null với bản ghi cũ), `chapter_flags_json` (TEXT, mặc định `[]`), `auto_confirm` (INTEGER 0/1, mặc định 1) | — | `(subject_code, uploaded_at)` |
| `pages` | `(document_id, page_number)` | `status`, `extraction_json` (`PageExtraction`), `confirmed`, `updated_at`, `version` | `document_id` → `documents` ON DELETE CASCADE | `(document_id, status)` |
| `page_audits` | `audit_id` INTEGER AUTOINCREMENT | `document_id`, `page_number`, `from_version`, `to_version`, `editor_id`, `changes_json`, `at` | `(document_id, page_number)` → `pages` ON DELETE CASCADE | `(document_id, page_number, audit_id)` |
| `chunks` | `chunk_id` | `document_id`, `subject_code`, `chapter_code`, `category`, `page_number`, `page_end`, `raw_text`, `verbatim_quote`, `tags_json`, `linked_chunk_ids_json`, `token_count`, `label_status`, `mapping_confidence`, `version`; thêm 2026-10-31 (GĐ1, migration `0005_concept_pair_preselect.sql`, đề xuất suy ra): `is_exercise` (INTEGER 0/1, null) | `document_id` → `documents` ON DELETE CASCADE | `(subject_code, chapter_code)`, `(document_id, page_number)` |
| `chunk_topics` | `(chunk_id, topic_key)` | — | `chunk_id` → `chunks` ON DELETE CASCADE | `(topic_key)` |
| `chunks_fts` (FTS5) | rowid | `body` (văn bản qua bước 1–3 của ADR-006 mục 5), `chunk_id` UNINDEXED, `subject_code` UNINDEXED; `tokenize = 'unicode61 remove_diacritics 0'` (ADR-006) | repository ghi cùng giao dịch với `chunks` | FTS5 |
| `chunks_fts_nodiac` (FTS5) | rowid | như trên, `tokenize = 'unicode61 remove_diacritics 2'`; chỉ dùng khi chỉ mục chính không trả kết quả (ADR-006 mục 5) | như trên | FTS5 |
| `concept_pairs` | `pair_id` | `chunk_id`, `subject_code`, `chapter_code`, `term`, `definition_quote`, `term_spans_json`, `status`, `proposed_by`, `mapping_confidence`, `created_at`, `confirmed_at`, `version`; thêm 2026-10-31 (GĐ1, migration `0005_concept_pair_preselect.sql`, đề xuất suy ra): `pre_selected` (INTEGER 0/1, mặc định 0), `failed_gates_json` (TEXT, mặc định `[]`) | `chunk_id` → `chunks` ON DELETE CASCADE | `(subject_code, status, chapter_code)` |
| `ingestion_items` | `item_id` | `source_doc_id`, `page_number`, `topic_key`, `approved`, `non_cloneable`, `item_json` (`IngestionItem` trừ `audits`), `updated_at`, `version` | `source_doc_id` → `documents` ON DELETE RESTRICT | `(source_doc_id, page_number)` |
| `patch_audits` | `audit_id` INTEGER AUTOINCREMENT | `item_id`, `from_version`, `to_version`, `editor_id`, `changes_json`, `at` | `item_id` → `ingestion_items` ON DELETE CASCADE | `(item_id, audit_id)` |
| `blueprints` | `blueprint_id` | `subject_code`, `blueprint_json` (`ExamBlueprint`), `created_at`, `version` | — | `(subject_code, created_at)` |
| `blueprint_items` | `(blueprint_id, slot_index)` | `item_id` | `blueprint_id` → `blueprints` ON DELETE CASCADE; `item_id` → `ingestion_items` ON DELETE RESTRICT | `(item_id)` |
| `twin_batches` | `batch_id` | `blueprint_id`, `base_seed` (INTEGER, < 2^63 nên vừa INTEGER 64 bit có dấu), `request_json`, `body_digest`, `idempotency_key`, `created_at` | `blueprint_id` → `blueprints` ON DELETE CASCADE | UNIQUE `(blueprint_id, idempotency_key)` WHERE `idempotency_key IS NOT NULL`; `(blueprint_id, created_at)` |
| `twins` | `twin_id` | `batch_id`, `slot_index`, `ordinal`, `twin_json` (`TwinQuestion`) | `batch_id` → `twin_batches` ON DELETE CASCADE | `(batch_id, slot_index, ordinal)` |
| `review_tickets` | `ticket_id` INTEGER AUTOINCREMENT | `batch_id`, `slot_index`, `ticket_json` (`ReviewTicket`) | `batch_id` → `twin_batches` ON DELETE CASCADE | `(batch_id, slot_index)` |
| `templates` | `template_id` | `topic_key`, `tier` (`candidate` \| `verified`), `provenance`, `source_sha256` UNIQUE, `generator_source`, `metadata_json`, `verification_json`, `approved_by`, `approved_at`, `created_at`, `version` | — | `(topic_key, tier)` |

- Cột `*_json` chứa `model_dump_json()` của model Pydantic tương ứng và được validate lại khi đọc; cột tách riêng chỉ để lọc và làm khoá.
- **GĐ0 (thêm 2026-10-31):** field mới của `PageExtraction` (`page_role`, `dropped`, `restored`, `role_evidence`, `own_images`, …; §5.7) chỉ nằm trong `pages.extraction_json`, không thêm cột và không thêm migration. Tóm tắt trang (`PageSummary.page_role`, `dropped`, và từ GĐ1 `confirmed_by`) đọc bằng `json_extract` như `tier_used`, `review_reasons`; JSON trang lưu trước GĐ0 thiếu các field này đọc ra giá trị mặc định (`content`, `false`). Field tự xác nhận của GĐ1 (`confirmed_by`, `confirm_rule`, `auto_confirm_undone`) cũng chỉ nằm trong `extraction_json`.
- **GĐ1 (thêm 2026-10-31):** hai migration. `0004_document_chapter_title.sql` (tên đã duyệt 2026-10-31) thêm các cột chương và `auto_confirm` của `documents` (bảng trên); gộp năm cột vào cùng migration đã duyệt là đề xuất suy ra. Nhánh chương tự động (W-C) sở hữu và viết TRỌN migration 0004, gồm cả cột `auto_confirm` mà nhánh tự xác nhận (W-F) dùng; W-F không thêm migration riêng và phải đi sau hoặc rebase lên W-C. Migration 0005 thuộc nhánh job đề xuất cặp (W-B) (đề xuất suy ra). `0005_concept_pair_preselect.sql` (đề xuất suy ra, chờ duyệt) thêm `chunks.is_exercise`, `concept_pairs.pre_selected`, `concept_pairs.failed_gates_json`. Job đề xuất cặp không có bảng (§5.8). Cài đặt "Tự xác nhận" lưu theo tài liệu (`documents.auto_confirm`), không có bảng cài đặt.
- **Idempotency 24 h (§6.3):** tra `(blueprint_id, idempotency_key)` với `created_at` trong 24 h. Bản ghi hết hạn được đặt `idempotency_key = NULL` trong cùng giao dịch trước khi ghi bản ghi mới, để giữ index UNIQUE.
- **Kiểm FTS5:** khi khởi động, đọc `PRAGMA compile_options`; thiếu `ENABLE_FTS5` thì dùng BM25 tự cài trên cùng bộ token (ADR-006 mục 4), không lỗi.
- **Khối, mục, ảnh cắt, ledger (ADR-010 mục 17; duyệt 2026-11-01 (ADR-010); tên bảng/cột là đề xuất suy ra).** Migration đánh số theo THỨ TỰ MERGE, liên tục, không khoảng trống: `0006_ai_cache.sql` = cache AI (V02; bảng chung có cột `kind`, V03 dùng `kind = "structure_proposal"`; schema bảng chốt ở X02b: ADR-009 bản cập nhật, merge SAU ADR-010; tới khi đó đường VLM không bật), `0007_blocks_sections.sql` = khối/mục/ảnh cắt/ledger (X09). Nhánh merge sau đổi số của mình sang số kế tiếp. X10 không có migration đánh số. 0007 thêm (dữ liệu cũ không mất, cột mới null/mặc định; tài liệu cũ không tự nhập lại, I4):

| Bảng / cột | Khoá chính | Cột chính | Quan hệ | Index |
|---|---|---|---|---|
| `documents` (thêm cột) | — | `layout_version` (INTEGER, null), `layout_settings_sha256` (TEXT, null) (§5.7 `DocumentImport`) | — | — |
| `pages` (thêm cột) | — | `geometry_json` (TEXT, null: `PageGeometry`, §5.7; không đọc vào response) | — | — |
| `blocks` | `(document_id, block_id)` | `page_number`, `reading_order`, `type`, `eligible` (INTEGER 0/1, mặc định 0; chỉ hàm `refresh_eligible_blocks` ghi, §5.15), `block_json` (`Block`) | `(document_id, page_number)` → `pages` ON DELETE CASCADE | `(document_id, page_number, reading_order)`, `(document_id, eligible)` |
| `sections` | `(document_id, section_id)` | `parent_id` (null ở gốc), `ordinal`, `section_json` (`Section`) | `document_id` → `documents` ON DELETE CASCADE | `(document_id, parent_id, ordinal)` |
| `crop_images` | `(document_id, block_id)` (khoá theo VÙNG; đề xuất suy ra (orchestrator)) | `crop_ref` (sha256 PNG = khoá FILE nội dung, nhiều hàng có thể chung một file), `page_number`, `bbox_json`, `width_px`, `height_px`, `size_bytes`, `created_at`; byte PNG ở `HOLOCRON_DATA_DIR/crops/<document_id>/<crop_ref>.png` | `document_id` → `documents` ON DELETE CASCADE (không khoá ngoại tới `blocks`: khối được ghi lại khi dựng lại; code xoá hàng của `block_id` không còn trong cùng giao dịch dựng lại) | `(document_id, crop_ref)` |
| `extraction_ledgers` | `(document_id, page_number)` | `balanced` (INTEGER 0/1), `ledger_json` (`ExtractionLedger`) | → `pages` ON DELETE CASCADE | `(document_id, balanced)` |
| `structure_proposals` | `(document_id, page_number)` | `input_sha256`, `model` (chỉ DB cục bộ, không ra response/log), `prompt_version`, `status` (`applied` \| `rejected`), `ops_json`, `created_at` | → `pages` ON DELETE CASCADE | — |
| `chunks` (thêm cột) | — | `section_id` (TEXT, null), `block_ids_json` (TEXT, mặc định `[]`) | — | — |

- **Chỉ mục tra cứu khối (ADR-010 mục 18; duyệt 2026-11-01 (ADR-010)).** KHÔNG nằm trong migration đánh số. Hai script có điều kiện kiểu `fts5_vault.sql`, chạy lại mỗi lần mở kho (`IF NOT EXISTS`): `fts5_blocks.sql` (bảng `blocks_fts`: `body` = token đã gấp dấu bằng Python nối bằng dấu cách, `block_id`, `document_id`, `subject_code` UNINDEXED; `tokenize = 'unicode61 remove_diacritics 0'` + cùng `tokenchars` của `fts5_vault.sql`; chạy khi có `ENABLE_FTS5`) và `fts5_blocks_trigram.sql` (bảng `blocks_fts_trigram`, `tokenize = 'trigram'`, `body` = văn bản đã gấp dấu, gộp khoảng trắng; chỉ chạy khi probe trigram đạt: tạo thử bảng ảo trigram trong `temp` thành công và `sqlite_version() ≥ 3.34.0`). Repository ghi/xoá chỉ mục trong cùng giao dịch với `refresh_eligible_blocks` và khi xoá tài liệu (bảng ảo không có khoá ngoại, dọn bằng code như `_delete_chunk_index`). Bật FTS sau khi đã có khối ⇒ backfill từ khối `eligible = 1` khi mở kho. Thiếu FTS5/trigram, và kho in-memory ⇒ đường lùi Python. `chunks_fts`, `chunks_fts_nodiac`, `fts5_vault.sql` không đổi.
- **Ảnh cắt (ADR-010 mục 19):** kho SQLite ghi file PNG TRƯỚC khi commit hàng `crop_images` (ghi file tạm trong cùng thư mục rồi đổi tên; giao dịch lỗi ⇒ file thành mồ côi, dọn khi mở kho); một file chỉ bị xoá (sau commit) khi không còn hàng `crop_images` nào có `crop_ref` đó, đếm bằng truy vấn, không giữ bộ đếm tham chiếu riêng (đề xuất suy ra (orchestrator)); xoá tài liệu xoá hàng trong giao dịch rồi xoá thư mục `crops/<document_id>/` sau commit; khi mở kho, xoá thư mục/file ảnh không có hàng tương ứng; hàng còn mà file mất ⇒ endpoint ảnh trả 404. Đường dẫn chỉ dựng từ `document_id` và `crop_ref` đã kiểm mẫu. Kho in-memory giữ byte trong instance store (không dict cấp module), trần tổng `memory_crop_max_bytes = 256 MiB` (đề xuất suy ra); vượt ⇒ khối giữ `crop_pending` + `crop_capped`.
- **Kết quả chấm tự luận** (§5.14) không được lưu trong MVP (đề xuất, chờ duyệt): chấm lại cùng câu trả lời cho cùng kết quả (tái lập của bộ chấm, §5.14), nên không cần bảng riêng. Lịch sử làm bài và ôn lại câu sai thuộc MVP-1 (chốt 2026-10-26, §1.5); bảng lưu và contract của nó là đề xuất riêng, chờ duyệt, chưa có trong bảng trên.
- **`templates`:** chỉ lưu template do LLM viết lúc chạy (bậc *ứng viên*, và bậc *đã kiểm chứng* khi người dùng duyệt trong app). Template trong `infrastructure/templates/` của repo vẫn nạp từ file, không chép vào DB, và vẫn là nguồn duy nhất của fallback `precomputed_verified` (§10). Các lớp kiểm chứng mà một template ứng viên phải qua trước khi người dùng duyệt: K1–K7 ở §10.1 (duyệt 2026-10-26, kèm điều kiện; §17 D6).
- **Mã lỗi của kho SQLite** (duyệt 2026-10-25, §6.0, §6.4): tranh ghi (`rowcount = 0` khi so version) ⇒ 409; khoá ngoại `ingestion_items.source_doc_id` không trỏ tới tài liệu có thật ⇒ 422; kho bận hoặc bị khoá sau khi hết số lần thử ⇒ 503.

### 5.14 Hình thức trả lời, môn học và chấm tự luận offline (quyết định 2026-10-25)

**Đã duyệt 2026-10-25:** `ESSAY` trong `QuestionFormat`; `question_kind` giữ `{computational, theory}`; `exam_format ∈ {mcq, essay, mixed}` theo môn; phạm vi "Trình bày khái niệm X" với đáp án tham chiếu là `definition_quote` của cặp đã xác nhận; chấm offline bằng độ phủ n-gram âm tiết (bigram, trigram) sau NFC; hiển thị "khớp x/y ý" và các ý còn thiếu, không có điểm tuyệt đối, kèm ghi rõ giới hạn: người học diễn đạt khác tài liệu thì có thể bị chấm sót ý; các field `matched_points`, `missing_points`, `total_points`, `ngram_coverage` theo từng ý, `grader_version`; cùng đầu vào thì cùng kết quả. **Duyệt thêm (tối 2026-10-25):** model `Subject` có `exam_format` (nơi lưu `exam_format`) và endpoint chấm `POST /v1/twins/{twin_id}/essay-grade` (§6.9) (duyệt 2026-10-25, brief người dùng). **Duyệt 2026-10-26:** giữ tên field `matched_count` và `total_points`; các tham số số học của bộ chấm làm giá trị ban đầu, hiệu chỉnh sau trên dữ liệu thật bằng script đánh giá opt-in: tối thiểu 4 âm tiết mỗi ý (`ESSAY_POINT_MIN_SYLLABLES`), trọng số bigram/trigram 0.5/0.5, ngưỡng khớp ý 0.5; bỏ n-gram thuộc tên khái niệm (`term`) khỏi phần chấm (bước 2 dưới đây), kèm thêm `term` vào câu chữ I4 ở §0 (§17 D11(b)). **Đề xuất (chờ duyệt):** mọi phần khác của mục này, gồm tên các model còn lại (`ExamFormat`, `EssayPoint`, `EssaySpec`, `NgramCoverage`, `EssayPointResult`, `EssayGradingThresholds`, `EssayGradeResult`), các quy tắc tách ý ngoài số âm tiết tối thiểu, tách thêm ý dài tại dấu phẩy (`ESSAY_POINT_MAX_SYLLABLES`), phép tính bằng `Fraction`, `ESSAY_ANSWER_MAX_CHARS`. Toàn bộ phần tự luận là "MVP nếu còn thời gian" (§1.5); lịch chốt 2026-10-26: không thuộc MVP-1, làm 30/10–2/11 (§16).

```python
class QuestionFormat(str, Enum):                 # domain/schemas.py
    MCQ4 = "MCQ4"; NUMERICAL = "NUMERICAL"
    ESSAY = "ESSAY"                              # thêm 2026-10-25

class ExamFormat(str, Enum):                     # giá trị đã duyệt; tên enum đề xuất
    MCQ = "mcq"; ESSAY = "essay"; MIXED = "mixed"

class Subject(StrictModel):                      # đã duyệt 2026-10-25: nơi lưu exam_format; bảng subjects (§5.13) và API (§6.8) đã triển khai (duyệt 2026-10-28)
    subject_code: str = Field(pattern=r"^[A-Z]{2,6}[0-9]{2,4}$")   # người dùng
    display_name: str = Field(min_length=1, max_length=200)        # người dùng; mặc định = subject_code khi tạo ngầm
    exam_format: ExamFormat = ExamFormat.MIXED   # người dùng; mặc định mixed khi tạo ngầm
    version: int = Field(default=1, ge=1)        # code: optimistic locking
    created_at: datetime                         # code
    updated_at: datetime                         # code
```

- **Đã triển khai (duyệt 2026-10-28):** `Subject` đúng như khối trên (`domain/schemas.py`; `display_name` ≤ 200 ký tự; `ExamFormat.default_theory_format()` cho mặc định `TheorySlotSpec.format`: `mcq` và `mixed` → `MCQ4`, `essay` → `ESSAY`). Môn tạo ngầm trong cùng giao dịch với bản ghi chính (tài liệu hay blueprint).
- **Tạo môn:** người dùng tạo qua §6.8, hoặc code tạo ngầm khi `POST /v1/documents` hay `POST /v1/blueprints` gặp `subject_code` chưa có (`display_name = subject_code`, `exam_format = mixed`).
- **`exam_format` chỉ có hai tác dụng:** làm mặc định cho `TheorySlotSpec.format` khi trường này null (`mcq` → `MCQ4`, `essay` → `ESSAY`, `mixed` → `MCQ4`), và để UI hiển thị. `exam_format` là dữ liệu do người dùng đặt cho môn, không phải tên môn cố định, nên không trái CLAUDE.md §1. Code KHÔNG ĐƯỢC tự đổi `ESSAY` thành `MCQ4`. Đổi `exam_format` không làm đổi blueprint hay twin đã tạo.

**Câu tự luận "Trình bày khái niệm X"** (`TwinQuestion.format = ESSAY`):

```python
class EssayPoint(StrictModel):
    point_index: int = Field(ge=0)               # code: thứ tự trong definition_quote, liên tục từ 0
    text: str = Field(min_length=1, max_length=600)
        # code: lát cắt liên tục definition_quote[start:end] (NFC) theo quy tắc tách ý dưới đây;
        # PHẢI là chuỗi con của definition_quote sau NFC + gộp khoảng trắng (cùng quy tắc so khớp với I3)
    syllable_count: int = Field(ge=1)            # code: số token âm tiết của text

class EssaySpec(StrictModel):                    # TwinQuestion.essay
    essay_kind: Literal["define_concept"]        # code; MVP chỉ có dạng "Trình bày khái niệm X"
    pair_id: str                                 # code: cặp đã xác nhận, chọn bằng random.Random(f"{generator_version}:{pool_sha256}:{seed}")
    term: str                                    # chép từ ConceptPair.term
    points: list[EssayPoint] = Field(min_length=1, max_length=40)   # code: tách từ ConceptPair.definition_quote
    splitter_version: str = Field(min_length=1, max_length=32)      # code: phiên bản quy tắc tách ý
```

- `question_kind = "theory"`; `options`, `correct_key`, `numerical_answer` là `null`; `distractor_to_misconception_map = {}`; `artifacts = []`; `trace` là `TheoryTrace`.
- `stem` do code ghép từ mẫu cố định, ví dụ "Trình bày khái niệm «`term`».", theo ngôn ngữ người dùng chọn (CLAUDE.md §4.3). LLM không soạn câu.
- `citation` là trích dẫn của cặp; `solution_explanation_vi` là `definition_quote` nguyên văn, vì đáp án mẫu là trích dẫn, không phải văn bản diễn đạt lại (CLAUDE.md §2.1).
- **Validator:** `TwinQuestion._format_rules` thêm nhánh `ESSAY` theo các luật trên, và kiểm mọi `essay.points[].text` là chuỗi con của `citation.verbatim_quote` theo đúng quy tắc so khớp của I3 ở §0: đưa cả hai chuỗi về NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con. Validator này đang cưỡng chế I2, nên các test I2 sẵn có PHẢI xanh mà không sửa (CLAUDE.md §9).
- **Nguồn đề:** slot `ESSAY` cần ít nhất một cặp đã xác nhận cùng `subject_code` và `chapter_code` với slot; không có thì `ReviewTicket` với lý do "không đủ căn cứ trong Vault" (ADR-007 mục 1).
- **Tái lập (I4, câu chữ hiện hành cho câu lý thuyết):** cùng `(generator_version, pool_sha256, seed)` ⇒ cùng cặp, cùng `stem`, cùng `points`. Dấu vân tay R6 (§6.3): `generator_version`, dạng câu `"essay_define_concept"` và `[pair_id]`.

**Tách ý từ `definition_quote` (đề xuất, chờ duyệt. Đã duyệt 2026-10-25: các ý tách nguyên văn từ `definition_quote` và là chuỗi con theo quy tắc so khớp I3 ở §0. Đã duyệt 2026-10-26: `ESSAY_POINT_MIN_SYLLABLES = 4` làm giá trị ban đầu):**
1. Đưa `definition_quote` về NFC. Ranh giới ý là các dấu `.` `;` `:` `!` `?` `…` khi theo sau là khoảng trắng hoặc hết chuỗi (nên `3.14` không bị cắt), xuống dòng, và đầu mục liệt kê ở đầu dòng (`•`, `-`, `+`, `*`, `a)`, `1.`, `1)`).
2. Mỗi đoạn là một lát cắt liên tục của chuỗi gốc, bỏ khoảng trắng và dấu câu ở hai đầu. Đoạn rỗng bị bỏ.
3. Đoạn có ít hơn `ESSAY_POINT_MIN_SYLLABLES` âm tiết được gộp với đoạn đứng trước (đoạn đầu tiên thì gộp với đoạn sau). Gộp hai đoạn kề nhau cho ra lát cắt từ đầu đoạn trước tới cuối đoạn sau, nên kết quả vẫn là chuỗi con. Lặp tới khi không còn đoạn ngắn hoặc chỉ còn một đoạn.
4. Đoạn dài hơn `ESSAY_POINT_MAX_SYLLABLES` âm tiết được tách thêm tại dấu phẩy, chỉ khi mọi phần sau khi tách vẫn đủ số âm tiết tối thiểu.
5. Đếm âm tiết bằng hàm tách token dùng chung với Vault (ADR-006 mục 5: NFC, chuẩn hoá vị trí dấu thanh, `casefold`, tách `\w+`).

**Chấm offline (đã duyệt 2026-10-25: độ phủ n-gram âm tiết bigram và trigram sau NFC ở bước 1 và 3, so từng ý để báo "khớp x/y ý" ở bước 4, không điểm tuyệt đối, chạy offline ở bước 5; đã duyệt 2026-10-26: bước 2 (bỏ n-gram thuộc `term`, §17 D11(b)), trọng số và ngưỡng ở bước 3–4 làm giá trị ban đầu, hiệu chỉnh sau; đề xuất (chờ duyệt): phép tính bằng `Fraction` ở bước 4):**
1. Câu trả lời của người học được đưa về NFC rồi tách token bằng cùng hàm ở bước 5 trên. `A₂`, `A₃` là tập bigram và trigram liên tiếp của toàn bộ câu trả lời.
2. Với mỗi ý `p`: `B_p`, `T_p` là tập bigram, trigram của ý. Bỏ các n-gram mà mọi token đều thuộc token của `term`, vì thân câu đã cho sẵn `term`; nếu bỏ xong mà `B_p` rỗng thì giữ tập gốc.
3. `bigram = |B_p ∩ A₂| / |B_p|`; `trigram = |T_p ∩ A₃| / |T_p|`, hoặc bằng `bigram` khi `T_p` rỗng; `combined = ESSAY_BIGRAM_WEIGHT · bigram + ESSAY_TRIGRAM_WEIGHT · trigram`.
4. Ý khớp ⇔ `combined ≥ ESSAY_POINT_MATCH_MIN`. Mọi phép tính và so ngưỡng dùng `fractions.Fraction` (ngưỡng đọc từ chuỗi thập phân), để kết quả không phụ thuộc làm tròn số thực; giá trị trả về là `round(float(x), 4)`.
5. Code thuộc Deterministic Core, chạy trong tiến trình backend, chỉ dùng thư viện chuẩn; không gọi LLM, không gọi mạng. Đây là bộ tính điểm của CLAUDE.md §2.1, không phải template, nên không chạy trong sandbox; I1 ở §0 cho phép đúng ngoại lệ này cho kết quả chấm câu trả lời của người học (duyệt 2026-10-25).

| Ngưỡng (field của model cấu hình ở `infrastructure/`, không phải biến môi trường; đề xuất) | Mặc định | Ghi chú |
|---|---|---|
| `ESSAY_POINT_MIN_SYLLABLES` | 4 | Ý ngắn hơn bị gộp. Giá trị ban đầu, duyệt 2026-10-26; **chưa hiệu chỉnh**, hiệu chỉnh sau trên dữ liệu thật |
| `ESSAY_POINT_MAX_SYLLABLES` | 40 | Ý dài hơn được tách tại dấu phẩy (đề xuất) |
| `ESSAY_BIGRAM_WEIGHT` / `ESSAY_TRIGRAM_WEIGHT` | 0.5 / 0.5 | Tổng PHẢI bằng 1. Giá trị ban đầu, duyệt 2026-10-26; **chưa hiệu chỉnh** |
| `ESSAY_POINT_MATCH_MIN` | 0.5 | Giá trị ban đầu, duyệt 2026-10-26; **chưa hiệu chỉnh**. Hiệu chỉnh bằng script đánh giá opt-in trên bộ câu trả lời tự viết (CLAUDE.md §2.2) |
| `ESSAY_ANSWER_MAX_CHARS` | 5000 | Câu trả lời dài hơn trả 422 |

**Kết quả chấm:**

```python
class NgramCoverage(StrictModel):                # code
    bigram: float = Field(ge=0.0, le=1.0)
    trigram: float = Field(ge=0.0, le=1.0)
    combined: float = Field(ge=0.0, le=1.0)

class EssayPointResult(StrictModel):
    point_index: int = Field(ge=0)               # chép từ EssayPoint
    text: str                                    # chép từ EssayPoint (nguyên văn tài liệu)
    ngram_coverage: NgramCoverage                # code

class EssayGradingThresholds(StrictModel):       # code: chép giá trị cấu hình lúc chấm
    point_min_syllables: int
    point_max_syllables: int
    bigram_weight: float
    trigram_weight: float
    point_match_min: float

class EssayGradeResult(StrictModel):
    twin_id: str                                 # đường dẫn request
    grader: Literal["offline_ngram"]             # code; "llm_judge" thêm khi có AnswerJudge (sau MVP)
    grader_version: str = Field(min_length=1, max_length=32)   # code: phiên bản bộ tách token, n-gram và công thức
    thresholds: EssayGradingThresholds           # code
    total_points: int = Field(ge=1)              # code: = len(TwinQuestion.essay.points); tên field giữ (duyệt 2026-10-26)
    matched_count: int = Field(ge=0)             # code: = len(matched_points), để frontend không tự đếm (I1); tên field giữ (duyệt 2026-10-26)
    matched_points: list[EssayPointResult]       # code: point_index tăng dần
    missing_points: list[EssayPointResult]       # code: point_index tăng dần
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")   # code: sha256 UTF-8 của câu trả lời sau NFC; không lưu văn bản
    citation: Citation                           # chép TwinQuestion.citation để UI hiện trích dẫn đối chiếu
    # validator: matched_count + len(missing_points) = total_points; point_index của hai danh sách
    # không trùng và phủ đúng 0..total_points-1
```

- **Tái lập ("cùng đầu vào thì cùng kết quả" đã duyệt 2026-10-25):** cùng `grader_version`, `thresholds`, `essay.term`, `essay.points` và câu trả lời (sau NFC) ⇒ `EssayGradeResult` giống từng byte (`model_dump_json`). Câu trả lời gõ ở dạng NFC hay NFD cho cùng kết quả. Model không có field thời gian để giữ tính chất này. `essay.term` có trong danh sách đầu vào vì bước 2 của phần chấm bỏ các n-gram thuộc `term`; câu chữ I4 ở §0 đã thêm `term` (duyệt 2026-10-26, §17 D11(b)).
- **Hiển thị (đã duyệt 2026-10-25):** "Khớp `matched_count`/`total_points` ý", danh sách `missing_points` (nguyên văn tài liệu) và trích dẫn `citation`. KHÔNG hiển thị `ngram_coverage` như điểm, KHÔNG quy ra thang điểm. UI PHẢI ghi giới hạn, ví dụ: "Chấm theo mức trùng từ với tài liệu. Nếu bạn diễn đạt khác, ý đúng vẫn có thể bị tính là thiếu; hãy tự đối chiếu với trích dẫn."
- **Nâng cấp khi có LLM (sau MVP):** `AnswerJudge` (ADR-007 mục 5) trả verdict từng ý kèm bằng chứng; code kiểm bằng chứng (NFC, chuỗi con) rồi tính `matched_points`/`missing_points` cùng dạng kết quả trên với `grader = "llm_judge"`. LLM không trả điểm.
- **Vị trí code (đề xuất):** hàm tách token dùng chung đặt ở `domain/` (chỉ thư viện chuẩn), bộ tách ý và bộ chấm ở `services/`.

### 5.15 Khối, mục, sổ bảo toàn và ảnh cắt (ADR-010; duyệt 2026-11-01 (ADR-010))

Model ở `domain/blocks.py` (X05 tạo). Contract đã duyệt theo phạm vi (file quyết định mục 3); tên field ngoài Phụ lục A của ADR-010, tên cờ mới và mọi ngưỡng là đề xuất suy ra. Bộ dựng là Deterministic Core (hàm thuần ở `services/`), chỉ dùng hình học, kiểu chữ, cấu trúc; không rẽ nhánh theo môn (CLAUDE.md §1).

```python
LAYOUT_VERSION = 1                                # hằng; PR nào đổi đầu ra của bộ dựng với tài liệu đã lưu thì tăng đúng 1 lần (ADR-010 mục 15)

BlockType = Literal["heading", "paragraph", "list_item", "table", "figure", "formula", "code", "caption",
                    "header_footer", "page_number"]   # đóng (ADR-010 mục 2)
BlockFlag = Literal[                              # đóng (ADR-010 mục 3); nguồn: code, trừ ghi khác
    "crop_pending",            # cần ảnh cắt mà chưa có (chưa render, hoặc kèm crop_failed/crop_capped)
    "crop_failed",             # render vùng lỗi hoặc ảnh không qua kiểm của tiến trình cha (§5.7 CropRequest)
    "crop_capped",             # vượt trần ảnh/trang, ảnh/tài liệu, hoặc trần byte ảnh in-memory
    "table_unresolved",        # lưới bảng không khép/ô mơ hồ ⇒ khối là figure
    "table_token_mismatch",    # ô bảng có token số/mã lệch đối chiếu: BLOCKING, bảng GIỮ cấu trúc (không thành figure),
                               # chặn tự xác nhận, không đủ điều kiện tới khi gỡ cờ (ReviewReason/409 chốt ở X02b)
    "boxed",                   # văn xuôi nằm trong khung vector (ADR-010 mục 10)
    "formula_text_unverified", # chữ công thức lấy nguyên văn lớp chữ, chưa kiểm cấu trúc toán
    "code_layout_uncertain",   # khối code lẫn font/dòng chồng; khoảng trắng dựng lại có thể sai
    "layout_uncertain",        # chồng lấn/không chắc ⇒ figure + ảnh cắt (ADR-010 mục 11)
    "reading_order_uncertain", # CHỈ cho khối có dòng/từ bbox = null, xếp theo thứ tự PDFium (không đặt được bằng XY-cut)
    "figure_label_text",       # figure có labels (chữ nhãn bên trong, không phải văn xuôi)
    "text_occluded",           # có từ thuộc dòng TEXT_OCCLUDED (GĐ0 0B(2))
    "low_text_contrast",       # có từ thuộc dòng LOW_TEXT_CONTRAST (GĐ0 0B(1))
    "font_encoding_suspect",   # có từ thuộc dòng FONT_ENCODING_SUSPECT (GĐ0 0B(3))
    "human_edited",            # có tham chiếu confirmed_line (dòng người dùng sửa hoặc gõ thêm)
    "too_short_for_chunk",     # khối < 8 ký tự không gộp được trong Section ⇒ không thành chunk (vẫn tra cứu thấy)
    "ai_proposal_applied",     # V03: đề xuất cấu trúc đã áp lên khối này
    "ai_proposal_rejected",    # V03: đề xuất của trang bị cổng từ chối (mọi khối của trang)
    "vlm_invalid",             # VLM trả JSON hỏng/khối lạ sau lần gọi lại (V02)
    "vlm_gate_rejected",       # chữ VLM không qua cổng đối chiếu OCR ⇒ khối là figure + ảnh cắt
]
SemanticLabel = Literal["definition", "example", "exercise"]   # chỉ V03 (LLM) gắn

class WordRef(StrictModel):                       # frozen; từ lớp chữ trong PageGeometry.lines (§5.7)
    kind: Literal["word"] = "word"; line_idx: int = Field(ge=0); word_idx: int = Field(ge=0)
class OcrWordRef(StrictModel):                    # frozen; từ OCR trong PageGeometry.ocr_lines (X11)
    kind: Literal["ocr_word"] = "ocr_word"; line_idx: int = Field(ge=0); word_idx: int = Field(ge=0)
class ConfirmedLineRef(StrictModel):              # frozen; ExtractedLine đã sửa hoặc gõ thêm (sau PATCH …/lines)
    kind: Literal["confirmed_line"] = "confirmed_line"; line_index: int = Field(ge=0)
SourceRef = Annotated[WordRef | OcrWordRef | ConfirmedLineRef, Field(discriminator="kind")]

class TableCell(StrictModel):
    text: str                                     # code: chữ các từ của ô theo quy tắc paragraph; "" ⇔ ô rỗng
    line_refs: list[SourceRef]                    # code: tập con của Block.line_refs, rời nhau giữa các ô
    row: int = Field(ge=0); col: int = Field(ge=0)            # code: ô góc trên-trái, 0-based
    rowspan: int = Field(default=1, ge=1); colspan: int = Field(default=1, ge=1)

class Block(StrictModel):
    block_id: str = Field(pattern=r"^blk_[0-9a-f]{16}$")      # code: theo nội dung nguồn (ADR-010 mục 3)
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    page_number: int = Field(ge=1)
    type: BlockType                               # code (bộ dựng), V03 đổi qua cổng ma trận
    bbox: tuple[float, float, float, float] | None
        # code: hợp hộp các từ nguồn (figure ảnh nhúng: hộp ảnh), ∈ [0, 1], gốc trên-trái, trang đã xoay;
        # null chỉ khi mọi tham chiếu là dòng gõ thêm hoặc từ không có hộp; khối bbox null không cắt ảnh, không mang
        # crop_pending (đề xuất suy ra (orchestrator))
    text: str | None = Field(default=None, max_length=50_000)
        # code: dựng theo type (quy tắc dưới); null ⇔ type ∈ {table, figure}. source = "vlm": chữ VLM ĐÃ QUA cổng (NFC)
    line_refs: list[SourceRef] = []               # code; figure ⇒ PHẢI rỗng (chữ trong figure chỉ ở label_line_refs)
    label_line_refs: list[SourceRef] = []         # code: chỉ figure; từ nằm trong khung/vùng không chắc
    labels: list[str] = []                        # code: chỉ figure; labels[i] = chữ dòng nguồn thứ i (quy tắc paragraph)
    level: int | None = Field(default=None, ge=1, le=6)        # code/X08, V03: chỉ heading (bắt buộc ở heading)
    list_depth: int | None = Field(default=None, ge=0, le=8)   # code, V03: chỉ list_item (bắt buộc ở list_item)
    cells: list[list[TableCell]] | None = None    # code (X07): chỉ table (bắt buộc ở table); mỗi hàng liệt kê ô BẮT ĐẦU ở hàng đó
    caption_of: str | None = None                 # code (X07): chỉ caption; block_id của table/figure/formula được chú thích
    semantic_label: SemanticLabel | None = None   # LLM (V03) qua cổng; offline luôn null
    flags: list[BlockFlag] = []                   # code; sắp theo thứ tự khai báo, không trùng
    crop_ref: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")   # code: CropImage.crop_ref
    source: Literal["text_layer", "ocr", "vlm"]   # code: nguồn chữ của khối
    ocr_text: str | None = None                   # code: chỉ source = "vlm": chữ OCR đối chiếu của vùng (quy tắc paragraph)
    reading_order: int = Field(ge=0)              # code: XY-cut (X05), V03 hoán vị qua cổng; liên tục 0..n-1 trong trang
    # validator: level ⇔ heading; list_depth ⇔ list_item; cells ⇔ table; label_line_refs/labels chỉ ở figure;
    # caption_of chỉ ở caption; ocr_text ⇔ source = "vlm"; type ∈ {table, figure, formula} và bbox khác null ⇒ crop_ref
    # khác null HOẶC flags có crop_pending; bbox null ⇒ không crop_pending; table_token_mismatch chỉ ở table; mọi
    # SourceRef của trang xuất hiện ở đúng một khối (kiểm ở ledger)

SectionFlag = Literal["untitled", "continued_across_pages", "level_uncertain"]

class Section(StrictModel):                       # cây theo TỪNG tài liệu (ADR-010 mục 4)
    section_id: str = Field(pattern=r"^sec_[0-9a-f]{16}$")    # code: sha256(doc, đường dẫn tiêu đề chuẩn hoá, thứ tự anh em)
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    parent_id: str | None                         # code; null ⇔ kind = "chapter"
    kind: Literal["chapter", "section"]           # code: gốc của tài liệu là "chapter"
    level: int = Field(ge=0, le=6)                # code (X08): 0 ở gốc, 1 mục, 2–6 mục con
    chapter_code: str | None = Field(default=None, pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")
        # code: chỉ gốc; = DocumentImport.chapter_code
    title: str | None = Field(default=None, min_length=1, max_length=300)
        # gốc: DocumentImport.chapter_title; mục: text của khối heading đầu (nguyên văn, cắt 300 ký tự); null ⇔ cờ untitled
    heading_block_ids: list[str] = []             # code: khối heading của Section (nhiều khi nối trang)
    block_ids: list[str] = []                     # code: khối thuộc TRỰC TIẾP Section này, theo (trang, reading_order)
    ordinal: int = Field(ge=0)                    # code: vị trí trong anh em
    page_start: int = Field(ge=1); page_end: int = Field(ge=1)  # code
    flags: list[SectionFlag] = []                 # code

LedgerDropReason = Literal["hidden"]              # lý do LOẠI THẬT khỏi lines (hiện chỉ chữ ẩn)
class LedgerDrop(StrictModel):
    reason: LedgerDropReason; count: int = Field(ge=0)
class LedgerTypeCount(StrictModel):
    type: BlockType; count: int = Field(ge=0)
class LedgerFlagCount(StrictModel):               # thông tin: ký tự vẫn thuộc khối, dòng nguồn mang cờ
    reason: Literal["occluded", "low_contrast"]; count: int = Field(ge=0)

LedgerBasis = Literal["pdfium", "confirmed_lines", "ocr"]
class LedgerBasisCount(StrictModel):              # một basis của trang (đề xuất suy ra (orchestrator): ledger theo TỪNG basis)
    ledger_basis: LedgerBasis                     # tên theo Phụ lục A4 (p''')
    source_count: int = Field(ge=0)
        # số đếm ĐỘC LẬP: pdfium ⇒ PageGeometry.pdfium_nonws_total (worker); confirmed_lines ⇒ tổng đơn vị của mọi
        # ExtractedLine của trang (code, từ PageExtraction.lines); ocr ⇒ PageGeometry.ocr_nonws_total (worker; với
        # vùng OCR/VLM trên trang lớp chữ: đếm trên từ OCR của các vùng đó)
    assigned_count: int = Field(ge=0)
        # code: tổng đơn vị của tham chiếu thuộc basis này (word → pdfium, ocr_word → ocr, confirmed_line → confirmed_lines;
        # trang có dòng sửa: tham chiếu word còn lại cũng tính vào confirmed_lines)
    dropped: list[LedgerDrop] = []                # code: pdfium ⇒ [hidden = hidden_nonws_dropped]; basis khác ⇒ []

class ExtractionLedger(StrictModel):              # một trang (ADR-010 mục 6)
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$"); page_number: int = Field(ge=1)
    unit: Literal["nonws_nfc_codepoint"] = "nonws_nfc_codepoint"
    bases: list[LedgerBasisCount] = Field(min_length=1)   # code: mỗi basis có mặt trên trang đúng một phần tử, theo thứ tự khai báo
    by_type: list[LedgerTypeCount] = []           # code: phân rã Σ bases.assigned_count theo loại khối
    flagged: list[LedgerFlagCount] = []           # code
    page_drop_reason: Literal["cover_or_course_nav", "chapter_nav", "build_step"] | None = None
        # code: = page_role khi trang bỏ mềm (build_step = "build_step_duplicate" của file quyết định mục 1)
    balanced: bool                                # code: MỌI basis có source_count == assigned_count + Σ dropped.count
    # validator: balanced khớp phương trình của từng basis; Σ by_type.count == Σ bases.assigned_count

class CropImage(StrictModel):                     # ADR-010 mục 8; một hàng = ảnh cắt của VÙNG một khối
    document_id: str = Field(pattern=r"^doc_[0-9a-f]{16}$")
    block_id: str = Field(pattern=r"^blk_[0-9a-f]{16}$")      # code: khoá theo vùng (đề xuất suy ra (orchestrator))
    crop_ref: str = Field(pattern=r"^[0-9a-f]{64}$")           # code: sha256 của byte PNG = khoá FILE nội dung (dùng chung được)
    page_number: int = Field(ge=1)
    bbox: tuple[float, float, float, float]       # code: vùng trên trang gốc ∈ [0, 1] (hộp khối, chưa nới lề)
    width_px: int = Field(ge=1, le=1024); height_px: int = Field(ge=1, le=1024)   # worker, kiểm lại ở tiến trình cha
    size_bytes: int = Field(ge=1, le=1_048_576)   # worker
    media_type: Literal["image/png"] = "image/png"
```

- **Nơi chạy và thứ tự** (ADR-010 mục 13; thứ tự đề xuất suy ra (orchestrator)): tiến trình cha, `services/`. Trang lớp chữ đọc xong ⇒ `services/block_pipeline.py` chạy các stage theo thứ tự chốt ở đó (X05 khối cơ sở, `heading` nhận `level = 1` sơ bộ → X07 bảng/hình vector → X06 code). Bước kết thúc lần đọc, sau 0D: số trang CẤP TÀI LIỆU (`services/page_numbers.py`, sau mọi trang) → X08 cây mục (đặt `level` thật) → V03 (tuỳ chọn) → X08 lại nếu V03 đổi cấp/loại → kiểm ledger → ảnh cắt SAU mọi đổi loại (`RegionRenderer` cho khối `crop_pending`; lỗi/trần ⇒ giữ `crop_pending` + `crop_failed`/`crop_capped`, không ném) → lưu khối, mục, ledger, `layout_version`, `layout_settings_sha256` → tên chương, tự xác nhận, chốt (§6.5). `layout_settings_sha256` = sha256 JSON chuẩn hoá của `{"layout_version": LAYOUT_VERSION, "fingerprints": [...]}` (đề xuất suy ra (orchestrator): `LAYOUT_VERSION` nằm trong hash), `fingerprints` = danh sách `fingerprint()` (`{"name", "version", "settings"}`) của các stage tất định theo thứ tự chạy (stage của `block_pipeline`, `page_numbers`, X08, phần tất định của cổng V03); stage nào dùng mẫu `infrastructure/lang/*.toml` thì `settings` của nó gồm `lang_patterns_sha256` = sha256 JSON chuẩn hoá của mẫu đã hợp (sửa toml ⇒ đổi hash); `settings` của XY-cut gồm `xy_cut_max_depth`, `xy_cut_max_nodes`. Hash không gồm cấu hình tầng đọc (`PageGeometry.version`, `reader_settings_sha256`) và kết quả AI (lưu riêng kèm model + `PROMPT_VERSION`). Sổ đăng ký cộng dồn `LAYOUT_HASH_REGISTRY: dict[int, str]` (version → hash của cấu hình mặc định) ở `domain/blocks.py`: PR tăng `LAYOUT_VERSION` thêm đúng một mục, không sửa mục cũ; test `test_layout_hash_registered_for_current_version` (đổi ngưỡng mà không tăng version ⇒ đỏ); mỗi version khi phát hành thêm một test MỚI `test_layout_hash_v<N>_pinned` ghim literal (thêm test, không sửa test cũ) (đề xuất suy ra (orchestrator)).
- **Stage** (đề xuất suy ra (orchestrator)): `class PageStage(Protocol)`: `name: str`; `fingerprint() -> dict[str, Any]`; `run(context: PageContext, blocks: list[Block]) -> list[Block]`; `PageContext` (frozen, nội bộ) = `document_id`, `page_number`, `geometry`, `extraction`, `lang: LangPatterns`. `PAGE_STAGES: tuple[PageStage, ...]` ở `block_pipeline.py`: `base_blocks` (X05) → X07 → X06 → `reading_order` (X05, LUÔN cuối, gán `reading_order` và id) → kiểm ledger. `page_numbers` là stage cấp tài liệu `run(pages: list[PageBlocks]) -> list[PageBlocks]`, idempotent (chạy lại thì đặt mọi ứng viên về loại gốc rồi áp luật). Hàm thuần `with_page_drop_reason(ledger, page_role) -> ExtractionLedger`, X09 gọi sau 0D. Khối `bbox = null` không bao giờ mang `crop_pending`.
- **Số trang hai bước** (đề xuất suy ra (orchestrator)): stage từng trang tạo khối ỨNG VIÊN riêng cho mỗi dòng có hộp nằm trong dải lề và khớp mẫu số trang; `services/page_numbers.py` (cấp tài liệu) đổi `type` của ứng viên đạt luật thành `page_number` và cập nhật `ledger.by_type` (id không chứa `type` nên không đổi).
- **`PageBlocks` nội bộ** (đề xuất suy ra (orchestrator)): kết quả pipeline một trang là model `PageBlocks` ở `services/block_pipeline.py` (không ra response; response khối của trang ở §6.10 tên `PageBlockList`), có field `layout_failed`; X09 gắn `LAYOUT_FAILED` từ field đó. Trang `LAYOUT_FAILED` ⇒ `blocks = []`. Ledger là `None` khi thiếu geometry hoặc stage lỗi trước khi đếm; khi đó X09 không lưu hàng `extraction_ledgers` cho trang (trang vẫn mang `LAYOUT_FAILED`).
- **Thứ tự đọc khi thiếu hộp** (đề xuất suy ra (orchestrator)): dòng/từ `bbox = null` đứng ngay sau khối có hộp kề trước nó theo thứ tự PDFium, mang cờ `reading_order_uncertain`; đứng đầu trang theo thứ tự PDFium ⇒ đứng đầu; trang không có hộp nào ⇒ toàn trang theo thứ tự PDFium. Chỉ chồng lấn hình học mới ⇒ `figure` + `layout_uncertain`; dòng bị che ở lại khối của nó với cờ `text_occluded`.
- **`PageGeometry` thiếu** (đề xuất suy ra (orchestrator)): geometry vượt trần (từ, dòng, byte) ⇒ `geometry = None`, trang vẫn đọc và xác nhận bình thường bằng dòng. Trang lớp chữ của tài liệu có `layout_version` khác null mà không có geometry ⇒ không có khối, trang nhận `LAYOUT_FAILED` và đi đường dòng.
- **Chọn model phẳng (OPEN cho người dùng; nợ LAYOUT-001 ở ADR-004):** `Block` là MỘT model phẳng có validator theo `type` (đúng danh sách field của ADR-010 Phụ lục A, X05 tạo theo đó), lệch quy tắc discriminated union của CLAUDE.md §2.3; field riêng theo loại chỉ có `level`, `list_depth`, `cells`, `labels`/`label_line_refs`, `caption_of`, `ocr_text`, validator cưỡng chế chúng. Đổi sang union cần sửa ADR-010 và tăng `LAYOUT_VERSION`.
- **Bảng có ô lệch** (đề xuất suy ra (orchestrator)): khối `table` có ô số/mã lệch đối chiếu giữ là `table` (giữ cấu trúc) + cờ BLOCKING `table_token_mismatch`, không thành `figure`; chặn tự xác nhận (§5.7) và loại khỏi tập đủ điều kiện tới khi cờ được gỡ. `ReviewReason`, 409 ở §6.5 và cách gỡ cờ chốt ở X02b (OPEN); tới đó đường VLM không bật nên cờ không phát sinh.
- **Đơn vị ledger:** số code point không trắng (`str.isspace()` sai) của NFC(từ), cộng theo từ; dòng xác nhận: của `ExtractedLine.text`. Đếm trên không gian chỉ số của `PageGeometry` (dòng thô), không trên `ExtractedLine`. Mỗi đơn vị của trang thuộc đúng một khối (qua `line_refs`, `label_line_refs`) hoặc nằm trong `dropped`. Ký tự header/footer thuộc khối `header_footer`, số trang thuộc khối `page_number`, chữ bị che/tương phản thấp thuộc khối có cờ (bảng lý do ở ADR-010 mục 6). Lệch dù 1 đơn vị ⇒ khối của trang bị bỏ, trang dùng đường dòng, ledger lưu `balanced = false`, trang nhận `LAYOUT_FAILED` (§5.7). Bộ dựng lỗi hoặc vượt trần (`MAX_PAGE_BLOCKS = 10 000`, đề xuất suy ra) xử lý như ledger lệch.
- **Dựng văn bản theo loại** (ADR-010 mục 7): `paragraph`, `list_item`, `heading`, `caption`, `header_footer`, `page_number` = các từ nguồn theo (dòng, từ) nối bằng MỘT dấu cách (xuống dòng ⇒ một khoảng trắng, gộp khoảng trắng), GIỮ gạch nối cuối dòng; `code` = giữ dòng (`\n`), khoảng trắng đầu dòng và giữa từ dựng lại từ toạ độ x theo bề rộng ký tự đơn cách (không vào ledger); `formula` = từ cùng dòng nối bằng dấu cách, dòng nối bằng `\n`, cờ `formula_text_unverified`; `table` = `text` null, chữ ở ô; `figure` = `text` null, `labels`. Chữ khối lớp chữ là NFC của từng từ (rồi nối). Khối `paragraph` dài quá 50 000 ký tự được tách tại ranh giới dòng; khối loại khác vượt `max_length` ⇒ trang `LAYOUT_FAILED`. Khối `source = "vlm"`: `text` = chữ VLM đã qua cổng (khớp OCR độc lập theo ngưỡng, token số/mã trùng tuyệt đối; ngưỡng cổng chốt ở X02b: ADR-009 bản cập nhật, merge SAU ADR-010; tới khi đó đường VLM không bật, không có khối `vlm`), `line_refs` trỏ từ OCR của vùng, `ocr_text` giữ chữ OCR; không qua cổng ⇒ `figure` + `vlm_gate_rejected`. OPEN cho X02b: chữ dòng xác nhận, cách `PATCH …/lines` tác động và basis ledger trên trang có vùng VLM chốt ở ADR-009 bản cập nhật; tới đó đường VLM không bật.
- **I3 (không đổi định nghĩa, §0):** văn bản khối lớp chữ là chuỗi con (sau NFC + gộp khoảng trắng) của nối `line_refs`; trích dẫn của chunk theo khối là chuỗi con của `raw_text` = các `text` khối nối bằng `\n\n`; trích dẫn trong khối `vlm` là chuỗi con của `text` khối sau NFC.
- **Id:** `block_id` = `"blk_"` + 16 hex đầu của sha256 JSON chuẩn hoá `["block", document_id, page_number, refs]`, `refs` = danh sách đã sắp `[kind, line_idx, word_idx, line_sha256]` (`confirmed_line`: `[kind, line_index, 0, line_sha256]`), `line_sha256` = sha256 UTF-8 của nội dung dòng nguồn (dòng thô: các từ NFC nối bằng một dấu cách; dòng xác nhận: `ExtractedLine.text`); figure dùng `label_line_refs`, khối `vlm` dùng từ OCR. Khối không có từ nào (ảnh nhúng, vùng thuần ảnh): `["block_noref", document_id, page_number, type, bbox × 1000 làm tròn, region_index]`, `region_index` đếm trong nhóm khối không có từ của trang theo (y0, x0, y1, x1) rồi `type`. JSON chuẩn hoá = `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. `section_id` = `"sec_"` + 16 hex của sha256 `["section", document_id, path]` (đề xuất suy ra (orchestrator)), `path` = danh sách cặp `[tiêu đề chuẩn hoá, sibling_ordinal]` của MỌI Section từ cấp 1 (con của gốc) tới chính nút đó; tiêu đề chuẩn hoá NFC + `casefold` + gộp khoảng trắng (không tiêu đề: `""`); gốc: `path = []`. Mục cùng tên lặp dưới các cha khác nhau ("Ví dụ", "Giải") hay mục không tiêu đề lồng nhau không trùng id (test `test_section_ids_unique`).
- **Section:** khối thuộc đúng MỘT Section (sâu nhất đang mở tại vị trí khối). Trang liền nhau có `heading` cùng tiêu đề (so sau NFC + `casefold` + gộp khoảng trắng, bỏ hậu tố nối trang) hoặc có hậu tố nối trang (`continuation_suffixes` của `infrastructure/lang/{vi,en}.toml`) ⇒ cùng Section, cờ `continued_across_pages`. Không có tiêu đề rõ ⇒ `title = null` + `untitled`. Cấp tiêu đề (X08): phân cụm cỡ/độ đậm khối `heading` toàn tài liệu + đánh số đa cấp + vùng tiêu đề template; chương theo cơ chế hiện có (§5.7 "Chương tự động").
- **Số trang** (ADR-010 mục 12): dòng trong dải lề trên/dưới `page_number_margin = 0.10`, ≤ 12 ký tự không trắng, khớp mẫu số trang, và hiệu (số − `page_number`) không đổi trên ≥ 3 trang ⇒ `page_number` (thắng `header_footer`). Mẫu có chữ ("Trang n", "Page n of m") ở `lang/*.toml`; bộ nạp nạp HỢP các tệp ngôn ngữ.
- **Tập khối đủ điều kiện** (ADR-010 mục 13; đề xuất suy ra (orchestrator)). MỘT luật cho cây, tra cứu khối và chunk, theo luồng settle thật (`services/document_import.py`, `settle_document`/`_settle_once`: kiểm `_ready_to_confirm` → `_ingest` cắt chunk vào Vault → đặt `status = "confirmed"`; Vault và tài liệu là hai repository, không chung giao dịch; khoảng giữa hai bước là cửa sổ `SETTLE_PENDING_DETAIL`, nhận biết bằng `vault.has_document_chunks`, KHÔNG có giá trị `status` riêng):
  - **Tài liệu đã đóng** ⇔ `status = "confirmed"` HOẶC Vault đã có chunk của tài liệu (cửa sổ settle-pending). Đây là điều kiện "người dùng đã xác nhận tài liệu".
  - **Khối ứng viên** ⇔ tài liệu không phải `past_exam`; trang `confirmed`, không `dropped`, không `failed`; `type ∉ {header_footer, page_number}`; khối không mang `table_token_mismatch`.
  - **Khối đủ điều kiện** ⇔ khối ứng viên VÀ tài liệu đã đóng.
  - **Thứ tự settle:** (1) `_ingest` cắt chunk từ khối ứng viên (cộng `build_chunks` cho trang không có khối), Vault nguyên tử, idempotent ⇒ tài liệu đã đóng; (2) `refresh_eligible_blocks(document_id)` (một hàm store, X09 sở hữu) đặt `blocks.eligible` + chỉ mục tra cứu khối trong một giao dịch của store; (3) đặt `status = "confirmed"`. Gọi lại settle chạy lại (1) (idempotent) và (2). Văn bản đóng khi Vault có chunk (`_check_text_open`), nên tập ứng viên không đổi giữa (1) và (2): sau (2), chunk, cây và tra cứu dùng ĐÚNG cùng tập khối, kể cả trong cửa sổ settle-pending. Dừng giữa (1) và (2) ⇒ cây/tra cứu tạm thấy ÍT khối hơn chunk, KHÔNG BAO GIỜ thấy khối của tài liệu chưa đóng; lần settle sau bù lại.
  - Trang người dùng xác nhận khi tài liệu chưa đóng CHƯA đủ điều kiện. Trước khi đóng, `…/unconfirm`, `…/restore`, `PATCH …/lines` chỉ đổi tập ứng viên (chưa khối nào đủ điều kiện); sau khi đóng chúng trả 409. Chunk (§12.1), chỉ mục tra cứu khối (§12.3) và cây (§6.10) CHỈ dùng tập này.
- **Cây từ khối đủ điều kiện** (đề xuất suy ra (orchestrator)): X08 dựng Section trên tập khối ỨNG VIÊN (cùng định nghĩa với tập dùng để cắt chunk); trang `dropped` không sinh Section; endpoint cây chỉ trả tài liệu đã đóng. Tập ứng viên đổi (`…/restore`, `PATCH …/lines`, gỡ cờ) ⇒ X08 chạy lại trong cùng đường xử lý. `PATCH …/chapter` cập nhật `chapter_code` và `title` của Section gốc (id không đổi).
- **Thứ tự chunk** (đề xuất suy ra (orchestrator), để `chunk_id` tất định): một bộ đếm `ordinal` chung; chunk sắp theo trang đầu, trong cùng trang theo `reading_order` của khối đầu; chunk `build_chunks` của dãy trang không có khối (`LAYOUT_FAILED`, OCR) chen đúng vị trí trang đầu của dãy, sau chunk theo khối bắt đầu ở trang trước đó.
- **Dựng lại sau `PATCH …/lines`** (chọn khuyến nghị của ADR-010 mục 13): trang được dựng lại từ dòng đã xác nhận: dòng thô không có mảnh `ExtractedLine` nào bị sửa giữ tham chiếu `word`; dòng thô có mảnh bị sửa và dòng gõ thêm dùng `confirmed_line`; khối chứa chúng có `human_edited`; ledger có phần tử `ledger_basis = "confirmed_lines"`; khối có tập tham chiếu không đổi giữ `block_id` và hàng `crop_images` của nó, khối đổi nhận id mới (hàng `crop_images` của id cũ bị xoá).
- **Đường dựng lại** (PATCH lines và dựng lại khi hash khác; đề xuất suy ra (orchestrator)), trong CÙNG giao dịch: stage từng trang (cho trang được dựng lại) → `services/page_numbers.py` CẤP TÀI LIỆU chạy lại trên toàn tài liệu → X08 → áp lại đề xuất V03 đã lưu (`structure_proposals`/cache) cho khối có id KHÔNG đổi; khối đổi id ⇒ bỏ phần đề xuất trỏ tới nó, KHÔNG gọi lại LLM (không gọi mạng trong giao dịch) → X08 lại nếu đề xuất áp được đổi cấp/loại → kiểm ledger → lưu. Sau commit: cắt ảnh cho khối cần ảnh mà chưa có, trong cùng `crop_document_budget_s` (§5.7); tới lúc đó khối giữ `crop_pending`. Kết quả BẰNG kết quả dựng mới từ cùng đầu vào, TRỪ phần đề xuất V03 bị bỏ vì id đổi. Test `test_page_numbers_rerun_after_patch`, `test_rebuild_equals_fresh_build_except_dropped_ai_proposals`. `layout_settings_sha256` đã lưu khác hash của code hiện tại ⇒ dựng lại mọi trang lớp chữ của tài liệu bằng code hiện tại và ghi `layout_version`/hash mới. Dựng lại (cả hai trường hợp) chỉ xảy ra khi tài liệu chưa đóng. Sau dựng lại (đề xuất suy ra (orchestrator)): trang tự xác nhận (`page_clean_v1`) được xét lại; có khối mang cờ chặn (§5.7) ⇒ trang bỏ tự xác nhận về xem lại (`confirmed = false`, `confirmed_by = null`, `confirm_rule = null`, `auto_confirm_undone` giữ `false`); trang người dùng xác nhận giữ nguyên, khối mới của nó theo luật đủ điều kiện chung. Test `test_rebuild_reevaluates_auto_confirmed_pages`.
- **Đề xuất cấu trúc của LLM/VLM (V03)** chỉ là thao tác tham chiếu id (`set_type`, `reorder`, `set_level`, `set_list_depth`, `set_semantic_label`), không có trường chữ; cổng và ma trận chuyển loại đầy đủ ở ADR-010 mục 14 (không bao giờ sang `figure`, `table`, `header_footer`, `page_number`; sang `heading` chỉ khi cỡ/đậm nằm trong cụm tiêu đề X08 đã nhận hoặc chữ khớp mẫu đánh số đa cấp; sang `code` chỉ qua hàm dựng X06; sang `formula` chỉ khi dựng lại từ `line_refs`). Vi phạm ⇒ bỏ cả đề xuất của trang, cờ `ai_proposal_rejected`.
- **I4 (không đổi định nghĩa, §0):** bảo đảm trên kết quả ĐÃ LƯU. Tài liệu cũ (`layout_version = null`) giữ `raw_text`/chunk; không tự nhập lại hay dựng lại khi `LAYOUT_VERSION` tăng. Cùng `PageGeometry` + cùng `layout_settings_sha256` (không bước AI) ⇒ cùng khối, mục, id, TRỪ field phụ thuộc kết quả cắt ảnh (`crop_ref`, cờ `crop_pending`/`crop_capped`/`crop_failed`) và quyết định tự xác nhận dựa trên chúng (cắt ảnh phụ thuộc ngân sách thời gian và worker; test tái lập so sánh khối đã bỏ các field đó; đề xuất suy ra (orchestrator)); hash gồm `LAYOUT_VERSION` và sha256 mẫu `lang/*.toml` đã hợp, nên tăng version hay sửa toml đều đổi hash. Kết quả AI lưu riêng (`structure_proposals`, cache AI) kèm model + `PROMPT_VERSION`, không tái lập được từ hash.
- **Test cưỡng chế đề xuất:** bảng ở ADR-010 mục 21 (chờ người dùng duyệt bổ sung bảng CLAUDE.md §9).

## 6. API Contracts

### 6.0 Quy ước chung
- Base URL backend: `http://127.0.0.1:8000`. Frontend gọi `/api/holocron/*`, được Next.js rewrite sang backend (cùng origin, không cần CORS). Bản desktop dùng phương án B của `docs/tauri-spike-plan.md` (duyệt 2026-10-26 chiều): backend FastAPI phục vụ luôn thư mục `out/` của Next static export, nên trang và API cùng origin `http://127.0.0.1:<port>`, vẫn không cần CORS. Phương án A (URL tuyệt đối + CORS) chỉ là dự phòng; dùng nó thì phải sửa quy ước "không cần CORS" này qua PR sửa spec.
- **Chế độ portable** (tên `HOLOCRON_WEB_DIR`, `HOLOCRON_STATIC_EXPORT`, `build:static`: tên duyệt 2026-10-27 (Q8); đã triển khai). Build bằng `npm run build:static` (base API `/`, không dữ liệu mẫu). Đặt `HOLOCRON_WEB_DIR` (§11.2) thì backend phục vụ thư mục export ở `/` và frontend gọi `/v1/...` cùng origin, không qua rewrite `/api/holocron/*`. Backend chèn `window.__HOLOCRON_RUNTIME__ = {"apiBase": "/"}` vào mọi file `.html` (gồm `404.html`) ngay sau thẻ mở `<head>` đầu tiên; file HTML không phải UTF-8 hợp lệ hoặc không có thẻ `<head>` thì trả nguyên văn, không chèn (HTML không phải UTF-8 có `Content-Type: text/html`, không kèm `charset`). `Cache-Control` theo code: HTML `no-store` (không `ETag`, không 304/206); `_next/static/*` khi 200 là `public, max-age=31536000, immutable`; file `*.txt` (payload RSC) `no-store`; file khác `no-cache`. Hành vi API giữ nguyên khi bật: đường dẫn thuộc `/v1`, `/docs`, `/redoc`, `/openapi.json` không bao giờ phục vụ file, không khớp route ⇒ 404 JSON `{"detail": "Not Found"}` như cũ; 405 kèm `Allow` và redirect dấu `/` cuối của API không đổi. Ngoài các tiền tố đó: method khác GET/HEAD ⇒ 405 kèm `Allow: GET, HEAD`; đường dẫn lạ ⇒ `404.html` với mã 404 (nếu có file); `/vault` redirect sang `/vault/` giữ query string. Token: Q1 chọn (a) (duyệt 2026-10-27, CHƯA triển khai; packaging-plan §2.3): khi đặt `HOLOCRON_WEB_DIR`, middleware miễn token cho GET/HEAD có path KHÔNG thuộc `/v1`, `/docs`, `/redoc`, `/openapi.json`; Host vẫn kiểm cho mọi request, kể cả file tĩnh (HTML, `_next/static/*`, `404.html`): middleware `LocalAccessMiddleware` chạy trước routing nên bao cả phần phục vụ web (đã triển khai, PR #81; §13.1); token sẽ được chèn vào `window.__HOLOCRON_RUNTIME__` (hiện chỉ có `apiBase`). Cho tới khi nhánh triển khai sửa §13.1, §13.1 (gồm dòng "mọi request PHẢI mang token") vẫn là contract hiện hành; bản portable hiện không đặt `HOLOCRON_REQUIRE_TOKEN`. Code hiện tại chưa có phần miễn token cho file tĩnh: khi đặt token thì middleware đòi token cho cả file tĩnh.
- **Token sai dạng (duyệt 2026-10-29 (câu 6), thay quyết định câu 21 (a) ngày 2026-10-28; câu 22 ngày 2026-10-28 giữ làm lớp thứ hai; đã triển khai ở PR sửa token, PR #97).** `HOLOCRON_REQUIRE_TOKEN` không đặt hoặc là chuỗi rỗng `""` ⇒ không yêu cầu token. Biến có giá trị mà toàn khoảng trắng (mọi khoảng trắng Unicode, kể cả dấu cách, tab, NBSP, xuống dòng) hoặc không hợp lệ theo `is_valid_token` (có ký tự ngoài ASCII in được U+0020–U+007E như NBSP, U+3000, `\n`, `\r`, ký tự điều khiển, hoặc khoảng trắng đầu/cuối quanh giá trị) là cấu hình hỏng: app TỪ CHỐI KHỞI ĐỘNG (kiểm ở lifespan trong `routers/api.py` và ở launcher; mã thoát tuỳ đường chạy, chi tiết ở §13.1), thông điệp chỉ nêu tên biến, không nêu giá trị. Lúc chạy (ví dụ biến bị đổi sau khi khởi động hoặc override), middleware vẫn trả 403 fail-closed cho mọi request (kể cả file tĩnh) với `detail` cố định chỉ nêu tên biến (code: "Yêu cầu bị từ chối: biến môi trường HOLOCRON_REQUIRE_TOKEN không hợp lệ"), như lớp thứ hai. Thứ tự kiểm: `Host` trước, rồi cấu hình token, rồi token. Chi tiết đối chiếu §11.2, §13.1.
- Payload JSON UTF-8, field snake_case. Thời gian ISO-8601 UTC.
- Lỗi luôn có dạng FastAPI `{"detail": ...}`, trong đó `detail` là một trong: chuỗi tiếng Việt; danh sách lỗi validate `[{loc, msg, type}]` (422); `{"message", "issues": [...]}` (409 khi approve còn BLOCKING); `CapabilityError` (409 khi thiếu khả năng, đã duyệt 2026-10-25, xem dưới); hoặc `{"code": "LLM_UPSTREAM_ERROR", "message"}` (502 khi gọi LLM lỗi, đã duyệt 2026-10-25, xem dưới).
- **Mọi endpoint chạy được khi không có LLM, trừ endpoint thực sự cần LLM** (đã duyệt 2026-10-25, §11.4). Quy tắc "503 khi thiếu key" của v0.5 bị bỏ. Endpoint cần một khả năng đang không khả dụng trả mã và `detail` dưới đây (mã và dạng `detail` đã duyệt 2026-10-25):

```python
class CapabilityError(StrictModel):              # đã duyệt 2026-10-25: detail của 409 khi thiếu khả năng
    code: Literal["CAPABILITY_UNAVAILABLE"]      # code
    capability: CapabilityName                   # code (§5.12)
    reason: CapabilityReason                     # code (§5.12; danh sách mã duyệt 2026-10-26, §17 D7)
    message: str                                 # code: câu tiếng Việt cố định, nêu tên biến cần đặt nếu có; không nêu giá trị
```

  Lý do chọn 409 thay vì 503: request hợp lệ và backend vẫn chạy bình thường; thứ thiếu là trạng thái cấu hình của app, không tự hết sau một lúc như 503 ngụ ý, và 503 dễ khiến client tự thử lại.

| Mã | Nghĩa trong Holocron |
|---|---|
| 200 / 201 | Thành công / tạo mới |
| 202 | Đã nhận tài liệu, đang đọc bất đồng bộ (`POST /v1/documents`, §6.5) |
| 204 | Đã xoá (`DELETE /v1/documents/{id}`, §6.5) |
| 400 | Yêu cầu hợp lệ về cú pháp nhưng vi phạm trạng thái nghiệp vụ (file rỗng; approve item non-cloneable; gắn non-cloneable cho item đã duyệt) |
| 403 | Đã duyệt 2026-10-25 (§13.1): `Host` không thuộc danh sách cho phép, hoặc thiếu/sai token khi đã đặt `HOLOCRON_REQUIRE_TOKEN`. Hướng duyệt 2026-10-26 chiều (§13.1; chi tiết là đề xuất (chi tiết), chờ duyệt): request ghi (`POST`, `PUT`, `PATCH`, `DELETE`) có `Origin` không thuộc danh sách origin cho phép. `detail` là chuỗi cố định, không lặp lại token hay header đã gửi |
| 404 | Không tìm thấy item / blueprint / batch / tài liệu / trang / chunk / cặp khái niệm / môn / twin |
| 409 | Xung đột trạng thái: `expected_version` lệch; sửa item đã duyệt; approve khi còn BLOCKING; blueprint chứa item chưa duyệt; sửa trang đã xác nhận hoặc đang đọc; xoá tài liệu còn được item tham chiếu; endpoint cần một khả năng đang không khả dụng (`CapabilityError`, đã duyệt 2026-10-25); với kho SQLite: tranh ghi (optimistic locking, `rowcount = 0`) ở `PATCH …/stem`, `POST …/non-cloneable`, `POST …/approve` (duyệt 2026-10-25, §6.4) |
| 413 | File vượt giới hạn của luồng: 10 MiB cho upload đề (§6.1); 50 MiB cho nhập tài liệu (§6.5, ADR-008 mục 4) |
| 415 | Định dạng file không hỗ trợ, hoặc `Content-Type` khai báo không khớp nội dung |
| 422 | Lỗi validate dữ liệu (kể cả `quantum` null/ngoài miền, topic chưa hỗ trợ); PDF có mật khẩu, không mở được hoặc quá 300 trang (§6.5); chỉ với kho SQLite: `source_doc_id` không tồn tại khi tạo item (`POST /v1/ingestion/items`, khoá ngoại ở §5.13; duyệt 2026-10-25) |
| 502 | Đã duyệt 2026-10-25: LLM đã cấu hình nhưng gọi lỗi sau khi hết số lần thử của adapter (timeout, gateway từ chối, JSON không hợp lệ; §11.5). `detail = {"code": "LLM_UPSTREAM_ERROR", "message"}`; không lộ địa chỉ endpoint, tên model hay nội dung phản hồi |
| 503 | Duyệt 2026-10-25: khi kho SQLite bận hoặc bị khoá (`database is locked`) sau khi hết số lần thử của repository (§5.13). `detail` là chuỗi tiếng Việt cố định (đề xuất); số lần thử là cấu hình của repository, chưa chốt. Không áp dụng cho kho in-memory. Quyết định câu 3, duyệt 2026-10-28 (thay mục "503 cho app đang tắt hoặc hàng đợi đóng" duyệt 2026-10-26 chiều): 503 chỉ dành cho phụ thuộc ngoài (kho bận). Bộ nhập tài liệu chưa khởi tạo, đang tắt hoặc hàng đợi nội bộ đã đóng ⇒ GIỮ 500 với `detail = IMPORT_UNAVAILABLE_DETAIL` ("Bộ nhập tài liệu đang dừng (app đang tắt); hãy gửi lại sau khi khởi động lại"; `services/document_import.py`), áp cho `POST /v1/documents`, `PATCH …/lines`, `POST …/pages/{page_number}/confirm`, `POST …/confirm` và `DELETE /v1/documents/{document_id}` (§6.5; theo code `routers/documents.py`) |

Quy tắc "503 khi thiếu key LLM" của v0.5 đã bị bỏ ngày 2026-10-25: thiếu LLM luôn là 409 `CapabilityError`, không bao giờ là 503. Holocron chỉ dùng 503 cho trường hợp kho SQLite bận (duyệt 2026-10-25), ở bảng trên; đó là trạng thái tạm thời nên client được phép thử lại. Hàng đợi nhập tài liệu nội bộ đóng không dùng 503 mà giữ 500 (câu 3, duyệt 2026-10-28).

Giới hạn upload theo luồng (đã duyệt 2026-10-25; không đổi hành vi hiện có): upload đề qua Gate (§6.1) giữ 10 MiB cho ảnh và PDF đề; bộ nhập tài liệu (§6.5) nhận PDF tới 50 MiB và 300 trang theo ADR-008. Hai luồng có endpoint, hằng giới hạn và mã lỗi riêng; `tests/test_input_limits.py` và `tests/test_upload.py` tiếp tục khoá hành vi của §6.1.

### 6.1 `POST /v1/ingestion/upload` — đã triển khai

Nhận file đề, chạy trích xuất dual-pass và Ingestion Gate, trả `document_id` cùng `GateReport`. Giới hạn 10 MiB của endpoint này giữ nguyên; tài liệu học và PDF nhiều trang đi qua bộ nhập ở §6.5 (50 MiB, 300 trang).

**Request** `multipart/form-data`:

| Field | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `file` | file | có | PNG, JPEG, WebP hoặc PDF (nhận diện bằng magic bytes); 1 byte – 10 MiB |
| `topic_key` | string | có | khớp `^[a-z0-9_]+(\.[a-z0-9_]+){2,}$`; extractor phải hỗ trợ |
| `quantum` | int | không | 1–20; nếu có thì ghi đè quantum đọc được từ đề |

**Xử lý (PHẢI theo đúng thứ tự):**
1. Đọc tối đa 10 MiB + 1 byte; vượt → 413. Rỗng → 400.
2. Xác định `media_type` bằng magic bytes; không nhận diện được → 415. `Content-Type` khai báo khác `media_type` (trừ rỗng hoặc `application/octet-stream`) → 415.
3. `document_id = "doc_" + sha256(content)[:16]`; lưu idempotent (tải lại cùng file giữ bản ghi đầu).
4. `DocumentExtractor.extract()` trả hai lượt đọc; topic chưa hỗ trợ → 422.
5. Tạo `IngestionItem` mới (mỗi lần upload một `item_id`), chuẩn hoá bảng thành `CPUSchedulingProblem`, self-solve bằng template đã kiểm chứng, tính `GateReport`.

**Response** `201 UploadResponse` (rút gọn):

```json
{
  "document": {"document_id": "doc_5158ccdeb93a5397", "media_type": "image/png", "size_bytes": 72, "...": "..."},
  "item": {
    "item_id": "ing_2b96221bc584",
    "problem": {"algorithm": "RR", "quantum": 4, "processes": [{"name": "P1", "arrival_time": 0, "burst_time": 2}]},
    "self_solve": {"status": "NO_MATCH", "solved_answer_display": "4.60 ms", "...": "..."},
    "report": {
      "table_id": "tbl_5158ccdeb93a", "table_version": 1, "status": "BLOCKED",
      "issues": [
        {"code": "LOW_CONFIDENCE", "severity": "warning", "row": 1, "col": 2, "alternatives": [], "message_vi": "Độ tin cậy 0.61 < 0.85."},
        {"code": "DUAL_PASS_MISMATCH", "severity": "blocking", "row": 1, "col": 2, "alternatives": ["8", "9"], "message_vi": "Hai lượt đọc VLM cho kết quả khác nhau."},
        {"code": "SELF_SOLVE_MISMATCH", "severity": "blocking", "row": null, "col": null, "alternatives": [], "message_vi": "AI tự giải ra … không khớp đúng một phương án …"}
      ]
    },
    "approved": false
  }
}
```

**Extractor:** Tuần 2 dùng `MockDualPassExtractor` — tất định, không gọi mạng, trả đề RR mẫu với lượt A đọc nhầm burst của P2 (`"8"`, confidence 0.61) và lượt B đọc đúng (`"9"`). Tuần 3 thay bằng adapter VLM (§11) qua `app.dependency_overrides[get_extractor]` / cấu hình, router không đổi.

### 6.2 `PATCH /v1/ingestion/items/{item_id}/table` — đã triển khai

Người dùng sửa ô bảng, có optimistic locking.

**Request** `application/json`:

```json
{"editor_id": "local_user", "expected_version": 1, "patches": [{"row": 1, "col": 2, "new_text": "9"}]}
```

| Field | Ràng buộc |
|---|---|
| `editor_id` | chuỗi, ghi vào `PatchAudit` |
| `expected_version` | ≥ 1; PHẢI bằng `table_primary.version` hiện tại |
| `patches` | ≥ 1 phần tử; `new_text` ≤ 64 ký tự; `col` < số header |

**Ngữ nghĩa:**
- Ô được sửa nhận `confidence = 1.0`, `verified_by_human = true`; ô đã xác nhận được bỏ qua kiểm tra `LOW_CONFIDENCE` và `DUAL_PASS_MISMATCH`, nhưng vẫn qua kiểm tra schema (`NOT_A_NUMBER`, `OUT_OF_RANGE`…).
- `table_primary.version` tăng đúng 1 cho mỗi request (không phụ thuộc số ô).
- Ghi một `PatchAudit {from_version, to_version, editor_id, changes: [{row, col, old, new}], at}`.
- Chuẩn hoá lại `problem`, chạy lại self-solve và `GateReport` trước khi trả về.

**Response:** `200 IngestionItem`. **Lỗi:** 404; 409 khi item đã duyệt hoặc `expected_version` lệch (body nêu version hiện tại — client PHẢI tải lại item rồi mới sửa tiếp); 422 khi body sai hoặc cột không tồn tại.

**Giới hạn đã biết:** store in-memory không đảm bảo nguyên tử giữa bước so version và bước ghi. Khi chuyển sang SQLite (Tuần 4) PHẢI so-và-ghi trong một câu `UPDATE … WHERE version = :expected` (BE-003). Bản v0.4 ghi dùng `version_id_col` của SQLAlchemy; từ 2026-10-25 (D5, đã duyệt) câu `UPDATE` này được viết thẳng bằng `sqlite3` của thư viện chuẩn (§5.13).

### 6.3 `POST /v1/blueprints/{blueprint_id}/twins` — contract chốt, triển khai T2-N3

Kích hoạt sandbox sinh bộ Twin Questions cho mọi slot của blueprint.

**Tiền đề:** `POST /v1/blueprints` với `BlueprintCreate` (§5.9) → `201 ExamBlueprint` (§6.7). Mọi item PHẢI `approved=true` và không `non_cloneable` (ngược lại 409). Mỗi item thành một `BlueprintSlot` tính toán; mỗi `TheorySlotSpec` thành một slot lý thuyết. Nguồn của từng field ở §5.9.

**Request** `application/json` — `TwinBatchRequest`:

```json
{"count": 3, "format": "MCQ4", "base_seed": 20260419}
```

- `count` 1–10, mặc định 3 (số Twin cho mỗi slot).
- `base_seed` không gửi ⇒ server dùng `int(sha256(blueprint_id)[:15], 16)` để kết quả tái lập được (I4).
- Header `Idempotency-Key` (tuỳ chọn): cùng key + cùng body trong 24 h PHẢI trả lại đúng `TwinBatch` đã sinh, không chạy lại sandbox.

**Xử lý:** slot lý thuyết đi qua bộ sinh tất định của ADR-007, không gọi sandbox hay `CitationProvider`. `format` của request chỉ ghi đè slot tính toán và chỉ nhận `MCQ4` hoặc `NUMERICAL` (gửi `ESSAY` ⇒ 422); slot lý thuyết giữ `format` của slot (`MCQ4` hoặc `ESSAY`, §5.14) (duyệt 2026-10-26 chiều, §5.6). Với mỗi slot tính toán và mỗi `k ∈ [0, count)`, gọi `produce_twin` với seed gốc của twin k là `seed_k = (base_seed + k·7919·40) mod SEED_SPACE`, trong đó `SEED_SPACE = 2**63` (§10). Mỗi twin nhận `gantt` từ `solve()`; `distractor_to_misconception_map` là nguồn duy nhất cho badge misconception trên UI. Slot không sinh được ⇒ một `ReviewTicket` trong `review_tickets`, không làm hỏng cả batch.

**Tách dải seed và loại câu trùng (R6, phương án B, người dùng chọn ngày 2026-10-23):**
- Các lần re-roll của twin k dùng `(seed_k + j·7919) mod SEED_SPACE`, với `j ∈ [0, 40)` (ngân sách re-roll ở §10). Bước giữa hai twin là `7919·40`, đúng bằng độ dài một dải, nên dải seed của các twin không chồng nhau.
- **Dấu vân tay của câu** (đã duyệt 2026-10-24): `sha256` của JSON chính tắc gồm `template_id` và `params` (kết quả `generate_params()` trả về từ sandbox), serialize bằng `json.dumps({"template_id": …, "params": …}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))` rồi mã hoá UTF-8. Phạm vi so trùng (đã duyệt 2026-10-24): mọi twin đã sinh trong cùng batch.
- **Câu lý thuyết (đã duyệt 2026-10-25):** dùng cùng cách chia dải seed; dấu vân tay là `sha256` của JSON chính tắc gồm `generator_version`, dạng câu và danh sách `pair_id` theo thứ tự phương án (câu `ESSAY`: một `pair_id`, §5.14).
- **Đã triển khai cho slot lý thuyết MCQ4 (duyệt 2026-10-28, quyết định 14(b); `services/twin_batch.py`):** twin k có `seed_k = (base_seed + k·7919·40) mod SEED_SPACE`, lần thử `j ∈ [0, 40)` dùng `(seed_k + j·7919) mod SEED_SPACE` (R6 đúng như trên). Bộ sinh không lộ `pair_id`, nên dấu vân tay thay thế là `sha256` của JSON chính tắc (`sort_keys=True, ensure_ascii=False, separators=(",", ":")`, UTF-8) gồm `generator_version`, `form`, bốn phương án theo thứ tự A–D và `citation.chunk_id` của đáp án đúng (thay cho "danh sách `pair_id`"; nợ ADR-004; vân tay này coi hai câu cùng đáp án và cùng bốn phương án là trùng, nhưng không phân biệt hai cặp khái niệm khác nhau cùng chunk và cùng văn bản phương án). Phạm vi so trùng: mọi câu lý thuyết đã sinh trong cùng batch. Câu trùng ⇒ thử j tiếp theo; hết dải ⇒ `ReviewTicket`, không sinh câu trùng. **Vault không nhất quán (theo code, PR B5a+F4, 2026-10-28; người dùng chưa duyệt riêng, là sửa lỗi 500 theo review, shape không đổi):** (1) cặp khái niệm trong pool ứng viên của môn mà chunk nguồn không còn ⇒ slot thành `ReviewTicket` với lý do `THEORY_MISSING_CHUNK_REASON` (chuỗi cố định: "Vault không nhất quán: có cặp khái niệm đã xác nhận của môn mà đoạn tài liệu nguồn không còn trong kho; cần đối soát lại Vault rồi sinh lại"), không sinh twin; (2) trích dẫn không còn khớp nguyên văn chunk (`QuoteMismatchError`, I3) ⇒ `ReviewTicket` với lý do `THEORY_QUOTE_MISMATCH_REASON` (chuỗi cố định: "Vault không nhất quán: trích dẫn của một cặp khái niệm không còn khớp nguyên văn đoạn tài liệu nguồn (I3); cần đối soát lại Vault rồi sinh lại"), giữ các twin đã sinh của slot. Ticket không chép topic, `chunk_id` hay trích dẫn. Các slot khác vẫn sinh; `POST …/twins` không trả 500 vì các trường hợp này. **Slot tính toán giữ stride cũ** `base_seed + k·7919` (R6 chưa áp cho slot tính toán, xem "Vấn đề đã biết" dưới).
- Câu có dấu vân tay trùng với một twin đã sinh thì re-roll tiếp trong dải của twin đó (j tiếp theo). Câu trùng không tính là `ill_posed` và không trừ lượt LLM. Hết dải mà vẫn trùng thì ghi lý do vào `review_tickets`, KHÔNG ĐƯỢC sinh câu trùng.
- **Ảnh hưởng tới I4:** cùng `(template_sha256, seed)` vẫn cho cùng câu. Cùng `(blueprint, base_seed)` vẫn cho cùng batch, nhưng batch khác với trước khi sửa. Twin k = 0 không đổi seed, nên `examples/` chỉ đổi nếu walkthrough sinh từ hai twin trở lên; việc này phải được xác nhận ở PR sửa R6. Số đo ở "Vấn đề đã biết" dưới đây là mốc so sánh trước và sau khi sửa.

**Response:** `201 TwinBatch`:

```json
{
  "blueprint_id": "bp_os_midterm_2025_de2",
  "base_seed": 20260419,
  "twins": [{
    "twin_id": "twin_…", "slot_index": 1, "format": "MCQ4", "seed": 20268338,
    "stem": "Cho các tiến trình sau … $q = 3$ ms …",
    "options": {"A": "11.25 ms", "B": "6.25 ms", "C": "2.25 ms", "D": "4.50 ms"},
    "correct_key": "B",
    "distractor_to_misconception_map": {
      "A": {"misconception_code": "WAIT_CONFUSED_WITH_TURNAROUND", "label_vi": "…", "rationale_vi": "…"},
      "C": {"misconception_code": "WAIT_EQUALS_FIRST_RESPONSE", "label_vi": "…", "rationale_vi": "…"},
      "D": {"misconception_code": "RR_TREATED_AS_FCFS", "label_vi": "…", "rationale_vi": "…"}
    },
    "gantt": [{"process": "P1", "start": 0, "end": 3}, {"process": "P2", "start": 3, "end": 6}],
    "solution_explanation_vi": "Gantt: … WT trung bình = 25/4 = 6.25 ms.",
    "citation": {"doc_id": "doc_os_lecture05_cpu_scheduling", "page_number": 14, "verbatim_quote": "…", "chunk_id": "chk_os05_rr_definition"},
    "trace": {"sandbox_duration_ms": 42, "llm_attempts": 1, "seed_rerolls": 1, "template_sha256": "8aa7…", "provenance": "llm_generated"},
    "review_status": "AUTO_PASS"
  }],
  "review_tickets": []
}
```

**Lỗi:** 404 blueprint; 409 blueprint chứa item mất trạng thái duyệt; 422 body sai (kể cả `format = "ESSAY"`, duyệt 2026-10-26 chiều). Lỗi template/sandbox KHÔNG trả 5xx — chúng đi vào `review_tickets`.

**Trạng thái triển khai (`routers/blueprints.py`, `services/twin_batch.py`; trước khi tách router ở PR #37 endpoint nằm trong `routers/api.py`):**
- `POST /v1/blueprints/{id}/twins` đã triển khai theo đúng các điểm trên (trừ R6 cho slot tính toán). Slot lý thuyết MCQ4 đã nối vào luồng này (duyệt 2026-10-28; `twins` là union theo `question_kind`, §5.11; mọi batch được lưu, kể cả khi không có `Idempotency-Key`). `POST /v1/blueprints` đã triển khai nhưng chỉ cho slot lý thuyết (§6.7, duyệt 2026-10-28); blueprint có slot tính toán vẫn nạp được qua `IngestionStore.save_blueprint(blueprint, item_ids)` (dùng trong test) và luồng sinh twin tính toán giữ nguyên.
- `TwinBatchRequest.format` chỉ nhận `MCQ4` hoặc `NUMERICAL` (`ESSAY` ⇒ 422) và chỉ ghi đè slot tính toán: đã có trong code.
- 409 kiểm mọi item gắn với blueprint (`item_ids` lưu ở store): thiếu, chưa `approved` hoặc `non_cloneable` đều là 409 (I5).
- `Idempotency-Key` có phạm vi theo `blueprint_id`; cùng key và cùng body (so sánh sau khi áp giá trị mặc định) trong 24 h trả lại đúng batch cũ, không chạy lại sandbox. Cùng key nhưng khác body được coi là request mới và không ghi đè bản ghi cũ (spec chưa định nghĩa trường hợp này).
- `format` của request chỉ ghi đè slot tính toán và trả 422 với `ESSAY` (duyệt 2026-10-26 chiều); code đã đổi cùng nhánh nối câu lý thuyết vào luồng twin (duyệt 2026-10-28).
- Mỗi slot không sinh được cho ra tối đa một `ReviewTicket`. Phụ thuộc ngoài đi qua `Protocol`: `CitationProvider` (mặc định `NoCitationProvider`, chưa có Knowledge Vault ⇒ mọi slot vào `review_tickets` với lý do thiếu nguồn trích dẫn) và `TemplateAuthor` (mặc định `UnconfiguredTemplateAuthor`, chưa có adapter LLM ⇒ pipeline rơi về template pre-computed, twin `AUTO_FALLBACK`).
- **Vấn đề đã biết (đã quyết định cách sửa, chưa sửa trong code):** code hiện vẫn dùng `base_seed + k·7919` cho twin k. Bước giữa các twin (`k·7919`) trùng bước re-roll seed (`7919`, §10), nên các twin trong cùng batch thường trùng seed và trùng đề. Cách sửa là phương án B ở trên. Đo lại ngày 2026-10-23 trên `develop` (`799ef72`): 30 batch × 3 twin, mỗi batch một slot template RR (author và nguồn trích dẫn giả lập như `tests/test_twin_batch.py`), `base_seed` mặc định theo `blueprint_id` `bp_measure_0` … `bp_measure_29`. Kết quả: 29/30 batch có seed trùng, 16/30 batch cả 3 twin cùng một seed; 74/90 twin trùng seed với ít nhất một twin khác trong batch, chỉ còn 45/90 twin khác nhau. Xem R6 ở §17.

### 6.4 Các endpoint khác (đã triển khai)

| Method | Path | Mô tả | Lỗi chính |
|---|---|---|---|
| POST | `/v1/ingestion/items` | Tạo item từ JSON kết quả VLM (dùng cho tích hợp/test) | 422 (kể cả `source_doc_id` không tồn tại, chỉ với kho SQLite; duyệt 2026-10-25) |
| GET | `/v1/ingestion/items/{id}` | Lấy item | 404 |
| PATCH | `/v1/ingestion/items/{id}/stem` | Sửa LaTeX stem (≥ 10 ký tự), chạy lại Gate | 404, 409 (đã duyệt; với kho SQLite, cả khi tranh ghi, duyệt 2026-10-25) |
| POST | `/v1/ingestion/items/{id}/non-cloneable` | Gắn cờ câu phụ thuộc hình vẽ (`reason` 5–300 ký tự) | 400 (đã duyệt), 404, 409 (tranh ghi, chỉ với kho SQLite; duyệt 2026-10-25) |
| POST | `/v1/ingestion/items/{id}/approve` | Duyệt | 400 (non-cloneable), 409 (còn BLOCKING; với kho SQLite, cả khi tranh ghi, duyệt 2026-10-25), 404 |
| PATCH | `/v1/ingestion/items/{id}/labels` | **Chưa triển khai, đã duyệt 2026-10-25 (§6.7):** sửa và xác nhận `ItemLabels` | 404, 422 |
| GET | `/v1/runtime` | Trạng thái chạy cho UI: `{"demo_mode", "llm_configured", "vlm_configured", "app_version", "commit", "log_dir", "capabilities"}`; ba cờ đầu là boolean, `capabilities: list[Capability]` gồm 12 phần tử theo §5.12 (nguồn: `services/capabilities.compute_capabilities(config, local_failures=...)`, `routers/runtime.py`; đã triển khai; `local_failures` là kết quả kiểm cục bộ một lần của `ocr` và `ocr_cross_check` do `services/capability_checks.py` tính, chạy nền sau khi server mở cổng và lưu ở `app.state`, request chỉ đọc lại; khi lần kiểm nền chưa xong, hai khả năng này báo `available = false`, `reason = "local_check_pending"` (duyệt 2026-10-30); các khả năng khác chỉ phụ thuộc cấu hình); không lộ khoá API, tên model hay endpoint. Code hiện: `demo_mode` đúng theo extractor đang chạy. Contract mới ở §5.12, chưa triển khai: `demo_mode` = thiếu LLM (đã duyệt 2026-10-23), `mock_extractor` (đề xuất, chờ duyệt, §17 D8); thêm chế độ lưu trữ và bên nhận dữ liệu LLM (hướng duyệt 2026-10-26 chiều; field `storage_mode`, `llm_data_recipient` là đề xuất (chi tiết), chờ duyệt); làm cùng F17 | — |

Với kho SQLite, mọi endpoint ghi ở bảng trên còn có thể trả 503 khi kho bận hoặc bị khoá sau khi hết số lần thử (§6.0; duyệt 2026-10-25).

### 6.5 Bộ nhập tài liệu PDF (ADR-008) — contract, chưa triển khai

Tên và đường dẫn endpoint ở mục này đã duyệt 2026-10-25. MVP chỉ nhận PDF (§1.6).

**Đọc bất đồng bộ (đã duyệt 2026-10-25).** Tầng 2 tốn 2–7 s/trang (ADR-008, "Kết quả đo"), nên tài liệu 300 trang có thể mất hàng chục phút; request đồng bộ sẽ quá timeout và khoá UI. Phương án đơn giản nhất cho app local: hàng đợi FIFO trong tiến trình backend, đọc một tài liệu mỗi lúc, mỗi trang qua worker có giới hạn (ADR-008 mục 3); client hỏi trạng thái bằng `GET` (khuyến nghị 1–2 s một lần). Không dùng WebSocket hay SSE, không thêm dependency. Khi backend khởi động lại, trang `pending` được xếp lại hàng đợi từ file gốc đã lưu (cần SQLite, §5.13); với store in-memory, tài liệu đang đọc bị mất.

#### `POST /v1/documents`

**Request** `multipart/form-data`:

| Field | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `file` | file | có | PDF (magic bytes `%PDF-`); 1 byte – 50 MiB; tên file ≤ 255 ký tự |
| `subject_code` | string | có | `^[A-Z]{2,6}[0-9]{2,4}$` |
| `source_kind` | string | có | giá trị của `SourceKind` (§5.8) |
| `chapter_code` | string | không (sửa đổi 2026-10-31, GĐ1 1A, duyệt 2026-10-31; trước đó bắt buộc) | `^ch[0-9]{1,2}(\.[0-9]{1,2})?$`, cùng mẫu với `KnowledgeChunk.chapter_code` (§5.8). Một file = một chương (duyệt 2026-10-26); lưu vào `DocumentImport.chapter_code` (§5.7). Không gửi (hoặc chuỗi rỗng) ⇒ code chọn theo §5.7 "Chương tự động" |
| `auto_confirm` | boolean (`true`/`false`) | không | Mặc định `true` (thêm 2026-10-31, GĐ1 1B, duyệt 2026-10-31). Lưu vào `DocumentImport.auto_confirm`; cài đặt "Tự xác nhận" của giao diện gửi qua field này (frontend tự nhớ lựa chọn; không có endpoint cài đặt riêng: đề xuất suy ra, ít đổi contract nhất) |
| `output_language` | string | không | `vi` (mặc định) hoặc `en` |
| `title` | string | không | 1–300 ký tự; mặc định tên file bỏ đuôi |

**Xử lý (PHẢI theo đúng thứ tự):**
1. Đọc tối đa 50 MiB + 1 byte; vượt → 413. Rỗng → 400. Tên file quá 255 ký tự → 422.
2. Magic bytes khác `%PDF-` → 415 (ảnh đề đi qua §6.1). `Content-Type` khai báo khác `application/pdf` (trừ rỗng hoặc `application/octet-stream`) → 415.
3. `document_id = "doc_" + sha256(content)[:16]`. Nếu tài liệu cùng nội dung đã được nhập qua §6.5: cùng `subject_code` → `200 DocumentImport` hiện có, không đọc lại; khác `subject_code` → 409 (đề xuất). Bản ghi chỉ do §6.1 tạo (chưa có `subject_code`) được bổ sung thông tin và đọc như tài liệu mới. Cùng nội dung, cùng `subject_code` nhưng khác `chapter_code` → 409 (duyệt 2026-10-26 chiều); muốn đổi chương thì sửa nhãn chunk (§6.6) hoặc xoá rồi nhập lại. `detail` của 409 này (`CHAPTER_CONFLICT`, duyệt 2026-10-28): "Tài liệu này đã được nhập cho một chương khác của môn này; muốn đổi chương thì sửa nhãn chunk hoặc xoá tài liệu rồi nhập lại". Bản ghi cùng nội dung nhập trước khi có `chapter_code` (`chapter_code` null) → 409 (duyệt 2026-10-26 chiều, §5.7). Từ 2026-10-31 (GĐ1 1A): form không có `chapter_code` mà cùng nội dung, cùng `subject_code` ⇒ `200 DocumentImport` hiện có (không tính lại chương, không 409); 409 `CHAPTER_CONFLICT` chỉ khi form có `chapter_code` khác bản ghi.
4. Thử mở trong worker (timeout 5 s, như tầng 1): cần mật khẩu để mở → 422 "PDF có mật khẩu" (không thử phá); không mở được hoặc quá hạn → 422 "không mở được tài liệu"; quá 300 trang → 422. PDF chỉ có mật khẩu chủ vẫn được nhận (ADR-008 mục 4).
5. Tạo ngầm môn `subject_code` nếu chưa có (§5.14, đề xuất). Form không có `chapter_code` ⇒ chọn chương và tên tạm theo §5.7 "Chương tự động" (thêm 2026-10-31; dưới đoạn găng chọn chương theo môn (code `ChapterReservations.critical_section`; duyệt 2026-11-01) để hai file nhập cùng lúc không nhận cùng chương trống: đề xuất suy ra). Lưu file gốc (§5.13), tạo `page_count` trang `pending`, xếp hàng đợi, trả `202 DocumentImport` (`status = "processing"`) kèm header `Location: /v1/documents/{document_id}`. App đang tắt hoặc hàng đợi nội bộ đã đóng → giữ 500 với `detail = IMPORT_UNAVAILABLE_DETAIL`, không tạo bản ghi (giữ 500, câu 3, duyệt 2026-10-28, §6.0).

**Đọc từng trang (ADR-008 mục 3, 6, 8, 9):** tầng 1 (timeout 5 s); tầng 2 (OCR cả trang, timeout 30 s) khi lớp chữ rỗng (`non_ws == 0`), hoặc lớp chữ rác (`text_score < TEXT_LAYER_MIN`, gồm TCVN3/VNI), hoặc trang ảnh (`non_ws < TEXT_SPARSE_CHARS` VÀ `image_area_ratio ≥ TEXT_SPARSE_IMAGE_MIN`), hoặc ảnh phủ gần hết trang với lớp chữ sạch (điều kiện (d): `non_ws < TEXT_FULL_IMAGE_MAX_CHARS=200` VÀ `image_area_ratio ≥ TEXT_FULL_IMAGE_MIN=0.90`, duyệt 2026-10-30), còn lại giữ lớp chữ; `image_area_ratio` của hai điều kiện ảnh chỉ tính ảnh không phải template, và `has_figure` trên PDF số chỉ tính ảnh và đường vẽ không-template, không-nền (sửa đổi 2026-10-31, GĐ0; luật 2026-10-30 "nhánh đường vẽ tính mọi ảnh" đã thay 2026-10-31); từ 2026-10-31 thêm điều kiện (e) `image_needs_reading` và các luật khử nhiễu, vai trò trang ở §5.7 "Khử nhiễu trang và vai trò trang" (ảnh template: ≥ `TEMPLATE_MIN_PAGE_RATIO` số trang VÀ ≥ 3 trang, cùng nội dung và vị trí; tài liệu 1–2 trang không có; một lượt quét nhẹ trước trang đầu, không render, vượt trần/quá hạn/lỗi thì không loại ảnh nào; duyệt 2026-10-30; ADR-008 mục 3, 6, 9; ngưỡng ở §5.7); `text_score = clean_char_ratio × valid_word_ratio` (Sửa đổi 2026-10-29, người dùng duyệt; ADR-008 mục 6); cờ trang chọn tầng 3 hoặc 4. Trang quá hạn hoặc lỗi thành `failed` (`PAGE_TIMEOUT`, `PAGE_ERROR`); trang có số dòng có chữ vượt giới hạn trang của Vault (5000) thành `failed` (`TOO_MANY_LINES`, `lines` rỗng; duyệt 2026-10-28); các trang khác vẫn đọc tiếp. Render tối đa 300 DPI và 25 megapixel mỗi trang.

**Sau khi đọc xong (thêm 2026-10-31, GĐ0 0D):** ở bước kết thúc lần đọc, service gán `page_role` và bỏ mềm trang theo §5.7 "Khử nhiễu trang và vai trò trang", trước khi tài liệu thành `review`; rồi đánh giá lại trang bìa của các tài liệu nhập trước cùng môn (cùng mục đó). Trong lúc tài liệu còn `processing`, mọi trang đã đọc có `page_role = "content"`, `dropped = false`. Thứ tự trong bước kết thúc (thêm 2026-10-31, GĐ1): 0D vai trò trang → ứng viên tên chương (§5.7 "Chương tự động") → tự xác nhận cấp trang (§5.7 "Tự xác nhận cấp trang") → ghi trạng thái cuối → chốt (settle; chỉ đóng tài liệu không có trang tự xác nhận, vì người dùng đã xác nhận mọi trang có văn bản trong lúc đọc; tài liệu đóng thì cắt chunk và tạo job đề xuất cặp, §5.7, §5.8) → ngoài khoá: đánh giá lại trang bìa của tài liệu trước, LLM chọn tên chương. Tài liệu có trang tự xác nhận dừng ở `review` cho tới khi người dùng bấm "Chấp nhận" (`POST /v1/documents/{document_id}/confirm`).

**Bất biến:** file là đầu vào không tin cậy, chỉ được mở trong worker có giới hạn (CLAUDE.md §2.2, ADR-008 mục 3). Không văn bản nào vào Vault hay Gate trước khi được xác nhận (ADR-008 mục 7): bởi người dùng, hoặc từ 2026-10-31 bởi luật tự xác nhận cấp trang chỉ cho trang không còn cờ (§5.7 "Tự xác nhận cấp trang"); I3 và I5 được kiểm sau bước này.

#### Đọc trạng thái và kết quả

| Method | Path | Response | Lỗi |
|---|---|---|---|
| GET | `/v1/documents?subject_code=` | `200 list[DocumentSummary]`, mới nhất trước; không phân trang (app một người dùng) | 422 (`subject_code` sai mẫu) |
| GET | `/v1/documents/{document_id}` | `200 DocumentImport` (§5.7), dùng để hỏi trạng thái | 404; 409 khi tài liệu nhập trước khi có `chapter_code` (`chapter_code` null; duyệt 2026-10-26 chiều, §5.7) |
| GET | `/v1/documents/{document_id}/pages/{page_number}` | `200 PageExtraction` (§5.7) | 404 (tài liệu không có, hoặc `page_number` ngoài `1..page_count`) |

```python
class DocumentSummary(StrictModel):              # đề xuất
    meta: DocumentMeta
    status: Literal["processing", "review", "confirmed", "failed"]
    pages_pending: int; pages_needs_review: int; pages_failed: int; pages_confirmed: int   # code: đếm từ pages
    pages_dropped: int                            # code (thêm 2026-10-31, GĐ0): số trang dropped = true
    # Sửa đổi 2026-10-31 (GĐ0; đề xuất suy ra, chờ duyệt): pages_needs_review đếm trang status = needs_review, KHÔNG
    # dropped và chưa confirmed, cùng định nghĩa với DocumentImport.pages_needs_review (§5.7); trước đó đếm mọi trang
    # status = needs_review
```

- **Màn kết quả nhập (T1e; thêm 2026-11-01).** Màn hình đọc `GET /v1/documents/{document_id}` (`DocumentImport`, §5.7): `pages_kept`, `pages_dropped`, `pages_needs_review`, `pages_auto_confirmed`, `pages_kept_unconfirmed`, `pages_failed`, `review_reason_counts`, `dropped_role_counts`, `proposal_job` hoặc `proposal_job_absent_reason`. Không thêm endpoint mới; `DocumentSummary` giữ nguyên (đề xuất suy ra, ít đổi contract nhất).

#### `PATCH /v1/documents/{document_id}/pages/{page_number}/lines`

Sửa văn bản của một trang trước khi xác nhận.

```json
{"editor_id": "local_user", "expected_version": 2,
 "edits": [{"line_index": 3, "text": "MOV R0, #0x1F"}], "appended_lines": []}
```

| Field | Ràng buộc |
|---|---|
| `expected_version` | PHẢI bằng `PageExtraction.version` hiện tại |
| `edits` | `line_index` phải tồn tại, không trùng; `text` ≤ 2000 ký tự; `text` rỗng hoặc chỉ gồm khoảng trắng nghĩa là dòng không vào văn bản xác nhận (dòng có chữ: xem định nghĩa ở §5.7; theo code, PR #83) |
| `appended_lines` | ≤ 500 dòng, mỗi dòng ≤ 2000 ký tự; thêm vào cuối trang với `engine = "human"`, `bbox = null`, `edited_by_human = true`, `conf = 1.0` (duyệt 2026-10-26 chiều) |
| — | `edits` và `appended_lines` không cùng rỗng |

**Ngữ nghĩa:** văn bản được chuẩn hoá NFC. Dòng được sửa nhận `edited_by_human = true` (duyệt 2026-10-26), xoá `flags`, và các `TokenMismatch` của dòng đó bị bỏ khỏi `cross_check`. Code tính lại `cross_check.status`, `review_reasons` và `status`; `version` tăng đúng 1. Dòng được sửa giữ nguyên `engine` và `conf` gốc của engine (không đổi thành `"human"`, không ghi đè `conf = 1.0`) (duyệt 2026-10-26 chiều). Quy tắc tính lại `review_reasons` dưới đây dựa vào `edited_by_human`. Ghi một `PageEditAudit` (đề xuất): `{document_id, page_number, from_version, to_version, editor_id, changes: [{line_index, old, new}], at}`.

**Tính lại `review_reasons` sau khi sửa thành công (duyệt 2026-10-26):**
- Bỏ `TOKEN_MISMATCH` khi `cross_check.mismatches` không còn phần tử nào (không còn token lệch).
- Bỏ `LOW_CONF_LINES` khi không còn dòng nào có `conf < LINE_CONF_MIN`; dòng có `edited_by_human = true` không tính là dòng conf thấp, bất kể `conf` ("dòng người dùng sửa không tính conf thấp"). Dòng gõ thêm cũng không tính, vì có `edited_by_human = true` và `conf = 1.0` (duyệt 2026-10-26 chiều). Dòng đã sửa giữ `conf` cũ của engine (duyệt 2026-10-26 chiều), nên `conf` vẫn cho biết engine đọc dòng đó chắc đến đâu.
- Thêm 2026-10-31 (GĐ0; đề xuất suy ra, chờ duyệt): bỏ `LOW_TEXT_CONTRAST`, `TEXT_OCCLUDED`, `FONT_ENCODING_SUSPECT` khi không còn dòng nào mang cờ dòng cùng tên; dòng được sửa đã bị xoá `flags` (ở trên) nên không còn mang cờ đó. Sửa dòng không đổi `is_header_footer`.
- Các lý do cấp trang khác (`LOW_TEXT_SCORE`, `LOW_OCR_SCORE`, `LOW_LINE_COVERAGE`, `HIGH_INK_OUTSIDE_TEXT`, `FIGURE_ON_SCAN`, `FORMULA_ON_SCAN`, `TIER3_UNAVAILABLE`, `CROSS_CHECK_UNAVAILABLE`, `HIGH_PAGE_CER`, `OCR_ENGINE_MISSING`, và từ 2026-10-31 `IMAGE_NEEDS_READING`, `PAGE_ROLE_UNCERTAIN`, `DENOISE_UNAVAILABLE`) giữ nguyên.
- `status` tính lại từ `review_reasons` theo validator ở §5.7: rỗng ⇒ `ok`, không rỗng ⇒ `needs_review`.
- Thêm 2026-11-01 (ADR-010 mục 13; duyệt 2026-11-01 (ADR-010)): trang có khối được dựng lại từ dòng đã xác nhận trong cùng giao dịch (§5.15 "Dựng lại sau `PATCH …/lines`"); `LAYOUT_FAILED` được tính lại theo kết quả dựng lại và ledger mới; `refresh_eligible_blocks` chạy cho trang.

**Response:** `200 PageExtraction`. **Lỗi:** 404; 409 khi `expected_version` lệch, trang còn `pending`, trang `failed` (không sửa được trong MVP, người dùng nhập lại file theo §5.7; duyệt 2026-10-26), đã xác nhận, hoặc đang bỏ mềm (`dropped = true`; thêm 2026-10-31, GĐ0, `detail` đề xuất: "Trang đang bị bỏ qua; hãy hoàn tác trước khi sửa"); 409 `SETTLE_PENDING_DETAIL` ("Văn bản của tài liệu đã vào Knowledge Vault nhưng tài liệu chưa hoàn tất xác nhận; hãy gọi lại xác nhận tài liệu, không sửa thêm được") khi Vault đã có chunk của tài liệu mà tài liệu chưa `confirmed` (duyệt 2026-10-28); 422 khi body sai, `line_index` không tồn tại, hoặc trang sau sửa vượt 5000 dòng (`MAX_PAGE_LINES`; theo code `apply_line_edits`, tổng số dòng hiện có cộng `appended_lines` tính mọi dòng, kể cả dòng rỗng; PR #83). Bộ nhập chưa sẵn sàng: 500 như §6.0.

#### Xác nhận

| Method | Path | Body | Ngữ nghĩa | Lỗi |
|---|---|---|---|---|
| POST | `/v1/documents/{document_id}/pages/{page_number}/confirm` | `{editor_id, expected_version}` | Nhận trang `ok` và `needs_review`; với trang `needs_review`, xác nhận nghĩa là người dùng đã xem lại (duyệt 2026-10-26). `confirmed = true`, ghi `confirmed_at`; trả `200 PageExtraction`. Gọi lại trên trang đã xác nhận trả 200, không đổi gì | 404; 409 khi trang `pending`, trang `failed` (duyệt 2026-10-26), version lệch, trang không có dòng nào có văn bản (dòng header/footer không tính, thêm 2026-10-31), trang đang bỏ mềm (`dropped = true`; thêm 2026-10-31, GĐ0, `detail` đề xuất: "Trang đang bị bỏ qua; hãy hoàn tác trước khi xác nhận"), hoặc tài liệu nhập trước khi có `chapter_code` (duyệt 2026-10-26 chiều, §5.7); 409 `SETTLE_PENDING_DETAIL` khi Vault đã có chunk của tài liệu mà tài liệu chưa `confirmed` (duyệt 2026-10-28); 422 khi trang vượt giới hạn trang của Vault (tối đa 5000 dòng có văn bản, mỗi dòng tối đa 2000 ký tự; nay chỉ là kiểm tra phòng thủ vì trang quá nhiều dòng đã thành `failed` lúc đọc, `TOO_MANY_LINES`; duyệt 2026-10-28) |
| POST | `/v1/documents/{document_id}/confirm` | `{editor_id}` | Xác nhận mọi trang `ok` chưa xác nhận; trả `200 DocumentImport`. Xác nhận theo lô chỉ nhận trang `ok` (duyệt 2026-10-26, theo ADR-008 mục 7): trang `needs_review` phải được xác nhận từng trang; trang `failed` không xác nhận được; trang bỏ mềm (`dropped = true`) bị bỏ qua, không xác nhận và không chặn việc chốt (thêm 2026-10-31, GĐ0). Từ 2026-10-31 (GĐ1 1B) đây là nút "Chấp nhận" một lần của luồng nhập: trang tự xác nhận giữ nguyên `confirmed_by = "auto"`, trang `ok` còn lại nhận `confirmed_by = "user"`, và chỉ bước chốt chạy trong endpoint này mới đóng được tài liệu có trang tự xác nhận (§5.7 "Chốt trạng thái"); còn trang `needs_review` chưa xác nhận thì tài liệu giữ `review`, người dùng xác nhận các trang đó rồi bấm "Chấp nhận" lần nữa (đề xuất suy ra). Từ 2026-11-01 (T1b): lời gọi đóng được tài liệu thì, sau khi cắt chunk, tự tạo job đề xuất cặp (§5.8 "Job nền đề xuất cặp", `trigger = "document_confirmed"`) và response có `proposal_job` (`status = "queued"`); LLM không khả dụng ⇒ không tạo job, vẫn 200, `proposal_job_absent_reason = "llm_unavailable"`; idempotent: gọi lại trên tài liệu đã `confirmed` trả 200 với job hiện có (nếu còn), không tạo job trùng; tạo job lỗi không đổi kết quả xác nhận (§5.7 quy tắc `proposal_job`) | 404; 409 khi còn trang `pending`, hoặc tài liệu nhập trước khi có `chapter_code` (duyệt 2026-10-26 chiều, §5.7); 409 `SETTLE_PENDING_DETAIL` khi còn trang cần xác nhận mà Vault đã có chunk của tài liệu và tài liệu chưa `confirmed` (nếu không còn trang nào cần xác nhận thì chỉ chạy lại bước chốt, §5.7); trang vượt giới hạn trang của Vault bị bỏ qua (không 422; code ghi cảnh báo vào log), trang `failed` do `TOO_MANY_LINES` không chặn việc chốt và các trang còn lại xác nhận như thường (duyệt 2026-10-28) |

- MVP không có bỏ xác nhận (đề xuất), trừ hoàn tác tự xác nhận của trang `confirmed_by = "auto"` (`POST …/unconfirm` dưới đây; thêm 2026-10-31, GĐ1 1B); muốn đọc lại thì xoá tài liệu và nhập lại.
- Xác nhận từng trang hay theo lô ghi `confirmed_by = "user"` (thêm 2026-10-31). Đọc lại riêng một trang `failed` là việc sau MVP (duyệt 2026-10-26 chiều, §1.6).
- Khi mọi trang có văn bản đều đã xác nhận, `DocumentImport.status = "confirmed"`, kể cả khi tài liệu đang `failed`; bước chốt trạng thái chạy cả ở đường `failed` (duyệt 2026-10-26 chiều, quy tắc ở §5.7 "Chốt trạng thái tài liệu"). Ngoại lệ từ 2026-10-31: tài liệu có trang tự xác nhận chỉ được đóng bởi `POST /v1/documents/{document_id}/confirm` (§5.7). Tài liệu có `source_kind` khác `past_exam` khi đó được cắt chunk vào Vault (§12.1), rồi LLM đề xuất nhãn và cặp khái niệm nếu đã có key (§6.6). Đề xuất: tài liệu `past_exam` không cắt chunk; trang đã xác nhận được dùng để tạo item (dưới đây).
- **Nhãn ban đầu của chunk (duyệt 2026-10-26):** mọi chunk cắt từ tài liệu nhận `chapter_code = DocumentImport.chapter_code` (form ở trên, hoặc chương tự động §5.7 từ 2026-10-31), `category = THEORY`, `topic_keys = []`, `label_status = "proposed"`. Người dùng xác nhận hoặc sửa nhãn bằng `PATCH /v1/vault/chunks/{chunk_id}/labels` (§6.6), sau đó `label_status = "confirmed"`.
- **Khối (thêm 2026-11-01, ADR-010 mục 13; duyệt 2026-11-01 (ADR-010)):** khối chỉ đủ điều kiện khi tài liệu đã đóng (§5.15 "Tập khối đủ điều kiện"). Bước chốt tài liệu chạy theo thứ tự: cắt chunk từ khối ứng viên (`_ingest`) → `refresh_eligible_blocks` → đặt `confirmed` (không chung một giao dịch; đề xuất suy ra (orchestrator)). Xác nhận trang, `…/unconfirm`, `…/restore` trước khi tài liệu đóng chỉ đổi tập ứng viên. Tài liệu có `layout_version` khác null cắt chunk theo khối (§12.1).

#### `POST /v1/documents/{document_id}/pages/{page_number}/restore` (thêm 2026-10-31, GĐ0 0D; tên và đường dẫn là đề xuất suy ra, chờ duyệt)

Hoàn tác bỏ mềm một trang bằng một lời gọi (§5.7 "Khử nhiễu trang và vai trò trang").

```python
class PageRestoreRequest(StrictModel):
    editor_id: str = Field(min_length=1, max_length=128)   # người dùng, như PageConfirmRequest
    expected_version: int = Field(ge=1)                    # người dùng: PHẢI bằng PageExtraction.version hiện tại
```

**Ngữ nghĩa:** trang `dropped = true` ⇒ `dropped = false`, `restored = true`, `version` tăng đúng 1; `page_role` và `role_evidence` giữ nguyên để màn hình vẫn hiện lý do máy đã đề xuất; `review_reasons` và `status` không đổi. Trang trở lại như mọi trang chưa xác nhận: được tính vào `pages_needs_review` nếu `needs_review`, phải xác nhận (từng trang hoặc theo lô nếu `ok`) thì mới vào Vault, và không bao giờ bị bỏ mềm lại tự động (§5.7). Gọi trên trang không bỏ mềm (kể cả đã hoàn tác) ⇒ `200`, không đổi gì, không kiểm `expected_version` (như xác nhận lại trang đã xác nhận). Không ghi `PageEditAudit` (không đổi dòng nào; đề xuất suy ra).

**Response:** `200 PageExtraction`. **Lỗi:** 404 (tài liệu hoặc trang); 409 khi `expected_version` lệch, khi tài liệu đã `confirmed` hoặc Vault đã có chunk của tài liệu mà tài liệu chưa `confirmed` (`SETTLE_PENDING_DETAIL`; văn bản tài liệu đã đóng, như PATCH …/lines), hoặc tài liệu nhập trước khi có `chapter_code` (§5.7); 422 khi body sai. Bộ nhập chưa sẵn sàng: 500 như §6.0.

#### `POST /v1/documents/{document_id}/pages/{page_number}/unconfirm` (thêm 2026-10-31, GĐ1 1B; tên và đường dẫn là đề xuất suy ra, chờ duyệt)

Hoàn tác tự xác nhận một trang bằng một lời gọi (§5.7 "Tự xác nhận cấp trang"). Body `PageUnconfirmRequest {editor_id, expected_version}` (ràng buộc như `PageConfirmRequest`).

**Ngữ nghĩa:** trang `confirmed_by = "auto"` ⇒ `confirmed = false`, `confirmed_at = null`, `confirmed_by = null`, `confirm_rule = null`, `auto_confirm_undone = true`, `version` + 1; dòng, `review_reasons`, `status` không đổi. Trang trở lại như trang chưa xác nhận và không bao giờ tự xác nhận lại. Gọi trên trang chưa xác nhận ⇒ `200`, không đổi gì, không kiểm `expected_version`.

**Response:** `200 PageExtraction`. **Lỗi:** 404; 409 khi trang đã xác nhận bởi người dùng (`confirmed_by` khác `"auto"`; MVP không có bỏ xác nhận của người dùng), `expected_version` lệch, tài liệu đã `confirmed` hoặc Vault đã có chunk của tài liệu (`SETTLE_PENDING_DETAIL` khi chưa `confirmed`), hoặc tài liệu nhập trước khi có `chapter_code`; 422 khi body sai.

#### `PATCH /v1/documents/{document_id}/chapter` (thêm 2026-10-31, GĐ1 1A; duyệt 2026-10-31, tên và body là đề xuất suy ra)

Đổi mã hoặc tên chương của tài liệu (§5.7 "Chương tự động").

```python
class DocumentChapterPatch(StrictModel):
    editor_id: str = Field(min_length=1, max_length=128)                # người dùng
    chapter_code: str | None = Field(default=None, pattern=r"^ch[0-9]{1,2}(\.[0-9]{1,2})?$")   # người dùng
    chapter_title: str | None = Field(default=None, min_length=1, max_length=300)              # người dùng; NFC, gộp khoảng trắng
    # validator: chapter_code và chapter_title không cùng null
```

**Ngữ nghĩa:** giá trị được đặt nhận nguồn `"user"` (`chapter_source.code` hoặc `.title`); đặt `chapter_code` xoá mọi `chapter_flags`. Không có `expected_version` (tài liệu không lộ `version` trong response); ghi dưới khoá của tài liệu, người ghi sau thắng (đề xuất suy ra). Đổi `chapter_code` không đổi chunk nào vì chỉ được phép khi Vault chưa có chunk của tài liệu. Thêm 2026-11-01 (ADR-010 mục 13; đề xuất suy ra (orchestrator)): tài liệu có khối thì `chapter_code`/`chapter_title` mới được ghi vào Section gốc (§5.15) trong cùng giao dịch; `section_id` không đổi. Đặt `chapter_code` trùng chương của tài liệu khác cùng môn được phép (như form).

**Response:** `200 DocumentImport`. **Lỗi:** 404; 409 khi đổi `chapter_code` mà tài liệu đã `confirmed` hoặc Vault đã có chunk của tài liệu (`chapter_title` vẫn đổi được lúc đó; request có cả hai trường thì không đổi gì và trả 409), hoặc tài liệu nhập trước khi có `chapter_code`; 422 khi body sai.

#### `POST /v1/documents/{document_id}/pages/{page_number}/items` (đã duyệt 2026-10-25)

Tạo `IngestionItem` từ một trang đề đã xác nhận, để đi tiếp qua Gate (§7.4).

```python
class PageItemCreate(StrictModel):
    topic_key: str = Field(pattern=TOPIC_KEY_PATTERN)   # người dùng chọn trong các topic có template đã kiểm chứng
    stem_latex: str = Field(min_length=10)              # người dùng chọn hoặc dán từ các dòng đã xác nhận
    options: dict[OptionKey, str]                       # như trên; MVP chưa có LLM tách đề
    table_id: str | None = None                         # một phần tử của PageExtraction.tables; null khi đề không có bảng
    solver_inputs: dict[str, int | float] = {}          # tham số vô hướng theo input_schema (§5.10)
```

Code (không phải người dùng) đặt `page_source = PageExtraction.tier_used` và `cross_check = PageExtraction.cross_check.status` cho item mới. Bắt buộc truyền `page_source` cho item đến từ PDF (quyết định (a), duyệt 2026-10-25); cơ chế bắt buộc bằng field không có mặc định, và validator đặt `cross_check = "unavailable"` khi `page_source` khác `"text_layer"` mà thiếu trạng thái đối chiếu, là đề xuất suy ra từ quyết định (a) (§5.4, §7.4).

**Response:** `201 IngestionItem`. **Lỗi:** 404 (tài liệu, trang hoặc `table_id`); 409 khi trang chưa xác nhận; 422 khi body sai hoặc topic chưa có template đã kiểm chứng.

#### `DELETE /v1/documents/{document_id}`

Xoá trang, chunk, chỉ mục tìm kiếm, cặp khái niệm và file gốc (ADR-006 mục 7); từ 2026-11-01 (ADR-010 mục 19; duyệt 2026-11-01 (ADR-010)) xoá cả khối, mục, ledger, `structure_proposals`, hàng chỉ mục tra cứu khối và ảnh cắt (hàng trong giao dịch, thư mục `crops/<document_id>/` sau commit). Twin đã sinh giữ trích dẫn vì trích dẫn là bản chụp. **Response:** 204. **Lỗi:** 404; 409 khi tài liệu đang `processing` hoặc còn `IngestionItem` tham chiếu (đề xuất).

### 6.6 Knowledge Vault (ADR-006, ADR-007) — contract, chưa triển khai

Tên và đường dẫn endpoint đã duyệt 2026-10-25. Mọi truy vấn bắt buộc lọc theo `subject_code` (ADR-006 mục 4). Mọi endpoint của mục này chạy offline, trừ `POST /v1/vault/concept-pairs/propose` (cần khả năng `concept_pair_llm_proposal`, §11.4).

| Method | Path | Request | Response | Lỗi |
|---|---|---|---|---|
| GET | `/v1/vault/chunks` | query `subject_code` (bắt buộc), `chapter_code`, `topic_key` (lặp được; chunk có ít nhất một khoá khớp), `label_status`, `offset` ≥ 0 (mặc định 0), `limit` 1–200 (mặc định 50) | `200 {"total": int, "chunks": list[KnowledgeChunk]}`, sắp theo `(doc_id, page_number, chunk_id)` | 422 |
| GET | `/v1/vault/search` | query `q` (1–200 ký tự), `subject_code` (bắt buộc), `topic_key` (lặp được), `limit` 1–50 (mặc định 20) | `200 list[SearchHit]` | 422 |
| PATCH | `/v1/vault/chunks/{chunk_id}/labels` | `{editor_id, expected_version, chapter_code?, category?, topic_keys?}` | `200 KnowledgeChunk` với `label_status = "confirmed"` | 404, 409 (version), 422 |

```python
class SearchHit(StrictModel):                    # đề xuất
    chunk: KnowledgeChunk
    score: float                                 # code: BM25; chỉ để xếp hạng, UI không cần hiển thị
    index: Literal["primary", "no_diacritics"]   # chỉ mục không dấu chỉ dùng khi chỉ mục chính rỗng (ADR-006 mục 5)
```

- **Tìm kiếm (ADR-006 mục 4–5):** truy vấn và văn bản chunk qua cùng hàm tách từ theo âm tiết (NFC, chuẩn hoá vị trí dấu thanh, `casefold`). Xếp theo BM25 giảm dần; hoà điểm thì theo `chunk_id`. Kết quả tất định.
- `PATCH …/labels` chỉ sửa nhãn; `raw_text` và `verbatim_quote` không sửa được ở đây vì văn bản đã được xác nhận ở §6.5. Sau khi sửa, `linked_chunk_ids` của mọi chunk cùng môn được tính lại mà không tăng `version` của chunk khác (duyệt 2026-10-26 chiều, §12.1). Giới hạn `topic_keys` của body và của tham số `topic_key`: tối đa 32 khoá, mỗi khoá ≤ 128 ký tự, khớp `TOPIC_KEY_PATTERN` (đề xuất (chờ duyệt), ghi theo code hiện có, §5.8, §17 D8).

**Cặp khái niệm (ADR-007 mục 1, 3):**

| Method | Path | Request | Response | Lỗi |
|---|---|---|---|---|
| POST | `/v1/vault/concept-pairs/propose` | `{subject_code, chunk_ids}` (1–20 chunk, cùng môn) | `200 {"pairs": list[ConceptPair], "rejected_count": int}`; cặp mới ở trạng thái `proposed` | 404 (chunk), 422, 409 `CapabilityError` khi `concept_pair_llm_proposal` không khả dụng (§6.0, thay 503), 502 `LLM_UPSTREAM_ERROR` |
| POST | `/v1/vault/concept-pairs` | `{editor_id, chunk_id, term, definition_quote}`: người dùng tự tạo bằng cách bôi đoạn trong chunk | `201 ConceptPair` với `proposed_by = "user"`, `status = "confirmed"` | 404, 422 (trích dẫn không khớp nguyên văn) |
| GET | `/v1/vault/concept-pairs` | query `subject_code` (bắt buộc), `chapter_code`, `status` | `200 list[ConceptPair]`, sắp theo `(chapter_code, term, pair_id)` | 422 |
| POST | `/v1/vault/concept-pairs/{pair_id}/confirm` | `{editor_id, expected_version, term?, definition_quote?}`; sửa rồi xác nhận | `200 ConceptPair` (`confirmed`) | 404, 409 (version), 422 (trích dẫn không khớp) |
| POST | `/v1/vault/concept-pairs/{pair_id}/reject` | `{editor_id, expected_version}` | `200 ConceptPair` (`rejected`) | 404, 409 |

- **Bất biến I3:** mọi `definition_quote`, do LLM đề xuất hay người dùng bôi, PHẢI là chuỗi con của `raw_text` của chunk sau khi đưa cả hai về NFC và gộp khoảng trắng. Đề xuất của LLM không khớp thì bị loại và chỉ được đếm trong `rejected_count`; trích dẫn người dùng nhập không khớp thì trả 422.
- **I4 (ADR-007 mục 2):** chỉ cặp `confirmed` cùng `subject_code` với slot vào `pool_sha256`. Xác nhận, sửa hay từ chối một cặp làm đổi câu lý thuyết sinh sau đó của đúng môn đó, không đổi môn khác.
- `propose` chạy đồng bộ, tối đa 20 chunk mỗi request, mỗi lần gọi LLM có timeout `HOLOCRON_LLM_TIMEOUT_S`. Dữ liệu rời máy: văn bản chunk gửi tới endpoint LLM đã cấu hình, hiện là gateway của ban tổ chức (§13.1). Giữ nguyên ở GĐ1 (tương thích); cặp tạo ở đây cũng nhận `failed_gates`, `pre_selected` theo §5.8 (đề xuất suy ra).
- `GET /v1/vault/concept-pairs` trả thêm `pre_selected`, `failed_gates` trong từng `ConceptPair` (§5.8; thêm 2026-10-31). Nhóm "Cần kiểm tra" trên màn hình là các cặp `proposed` có `failed_gates` không rỗng; frontend lọc theo field, không tự tính cổng. Chấp nhận vẫn qua `POST …/{pair_id}/confirm` từng cặp; GĐ1 không thêm endpoint chấp nhận theo lô (đề xuất suy ra).

**Job nền đề xuất cặp (GĐ1 1C; duyệt 2026-10-31; tên và đường dẫn endpoint là đề xuất suy ra, chờ xác nhận).** Model và luật ở §5.8 "Job nền đề xuất cặp".

| Method | Path | Request | Response | Lỗi |
|---|---|---|---|---|
| POST | `/v1/vault/concept-pairs/jobs` | `{subject_code, document_id?, chunk_ids?}`: đúng một trong `document_id` (mọi chunk của tài liệu) và `chunk_ids` (1–2000 chunk, cùng môn) | `202 ProposalJob` (`status = "queued"`, `trigger = "manual"`), header `Location: /v1/vault/concept-pairs/jobs/{job_id}` | 404 (tài liệu, chunk); 409 `CapabilityError` khi `concept_pair_llm_proposal` không khả dụng (§6.0); 409 khi tài liệu chưa có chunk; 422 (body sai, chunk khác môn, cả hai hoặc không có trường nào) |
| GET | `/v1/vault/concept-pairs/jobs` | query `subject_code` (bắt buộc) | `200 list[ProposalJob]`, mới nhất trước | 422 |
| GET | `/v1/vault/concept-pairs/jobs/{job_id}` | — | `200 ProposalJob` (client hỏi tiến độ 1–2 s một lần, như §6.5) | 404 (kể cả job đã mất khi khởi động lại) |
| POST | `/v1/vault/concept-pairs/jobs/{job_id}/cancel` | — | `200 ProposalJob`; gọi trên job đã kết thúc trả 200, không đổi gì | 404 |

- Job tự tạo khi xác nhận tài liệu (T1b, `trigger = "document_confirmed"`) đi qua cùng hàng đợi và hiện ở `GET …/jobs` như job gọi tay; trạng thái job mới nhất của một tài liệu cũng có ở `DocumentImport.proposal_job` (§5.7).
- **Trạng thái "không có LLM" của màn khái niệm (T1b mục 3; thêm 2026-11-01, đề xuất suy ra, chờ xác nhận):** nguồn DUY NHẤT là `GET /v1/runtime` (§5.12), khả năng `concept_pair_llm_proposal` có `available = false` (lý do `llm_not_configured` hoặc mã khác của `CapabilityReason`). Khi đó màn khái niệm hiện "Cần cấu hình LLM để tự đề xuất" và ẩn nút tạo job. `DocumentImport.proposal_job_absent_reason = "llm_unavailable"` chỉ là hệ quả của cùng khả năng đó cho màn kết quả nhập, không phải nguồn thứ hai.
- Job không gọi mạng khi tạo; lời gọi LLM chạy nền theo §5.8. Tiến độ (`batches_done`, `batches_failed`, `proposed_count`, `rejected_count`) do backend đếm; frontend chỉ hiển thị (I1).
- Dữ liệu rời máy như `propose` (§13.1).

### 6.7 Blueprint và batch twin (F11, F18) — `POST/GET /v1/blueprints` chỉ slot lý thuyết đã triển khai (duyệt 2026-10-28); phần còn lại là contract, chưa triển khai

#### `POST /v1/blueprints`

**Đã triển khai (duyệt 2026-10-28, câu 2 và 15; `routers/blueprints.py`): chỉ slot lý thuyết.** **Request** `BlueprintCreate` (§5.9). **Xử lý, theo thứ tự lỗi của code:**
1. Body sai (kể cả `item_ids` trùng, hai danh sách cùng rỗng, quá 50 phần tử, `format` ngoài `MCQ4`/`ESSAY`) → 422 (FastAPI).
2. `item_ids` khác rỗng → 422, `detail = "Blueprint có câu tính toán chưa được hỗ trợ trong bản này"`, trước mọi lần ghi và trước mọi kiểm khác. Không có 409 theo item ở đường này (bước 2–3 của contract mục tiêu dưới đây chưa chạy).
3. Mỗi `TheorySlotSpec.source_chunk_id` (nếu có) phải là chunk có thật, cùng `subject_code` của request và cùng `chapter_code` với slot, ngược lại → 422, `detail` nối các lỗi bằng `"; "`, mỗi lỗi nêu `theory_slots[i]`. Việc buộc chunk cùng `chapter_code` với slot là duyệt 2026-10-28 (câu 25) (§5.9 mục tiêu chỉ nói `chapter_code` của slot mặc định lấy từ chunk).
4. `format` null thì lấy mặc định từ `exam_format` của môn (môn chưa có ⇒ `mixed` ⇒ `MCQ4`, §5.14); code không bao giờ đổi `ESSAY` sang `MCQ4`.
5. Có slot `ESSAY` mà khả năng `essay_grading_offline` không khả dụng → 409 `CapabilityError`.
6. Lưu blueprint (môn chưa có thì tạo ngầm trong cùng giao dịch), trả `201 ExamBlueprint` với header `Location: /v1/blueprints/{blueprint_id}`. `source_exam_doc_id = null`; `slots` toàn `TheorySlot` (§5.9, "Contract đã triển khai").
- `GET /v1/blueprints?subject_code=`: `200 list[BlueprintSummary]`, mới nhất trước (`created_at` rồi `blueprint_id` giảm dần), có `batch_count` (§5.9); `subject_code` sai mẫu → 422; không phân trang. `GET /v1/blueprints/{blueprint_id}`: `200 ExamBlueprint`, 404 `"không tìm thấy blueprint"`.
- **Mọi batch twin được lưu** qua `TwinBatchRepository.save_batch` (kể cả khi không có `Idempotency-Key`; §6.3), nên `batch_count` đếm cả batch không có khoá.
- `GET /v1/blueprints/{blueprint_id}/twins` và `GET …/twins/{batch_id}` (F18, bảng dưới) CHƯA triển khai ở B5a.

**Contract mục tiêu sau 29/10 (nợ ADR-004; shape (a) của quyết định 14), còn hiệu lực cho phần `item_ids`.** **Request** `BlueprintCreate` (§5.9). **Xử lý (theo thứ tự):**
1. Body sai → 422.
2. Mỗi `item_id`: không tồn tại, chưa `approved` hoặc `non_cloneable` → 409, liệt kê các item lỗi (I5; cùng luật với §6.3).
3. Item có `labels.subject_code` khác `subject_code` của request, hoặc chưa có `labels.chapter_code` → 422, liệt kê item cần bổ sung nhãn (đề xuất).
4. `TheorySlotSpec.source_chunk_id` không tồn tại hoặc khác môn → 422.
5. Đề xuất (chờ duyệt, §5.14): tạo ngầm môn nếu chưa có; `TheorySlotSpec.format` null thì lấy mặc định từ `exam_format`. Có slot `ESSAY` mà khả năng `essay_grading_offline` không khả dụng → 409 `CapabilityError`; code không tự đổi slot đó sang `MCQ4`.
6. Dựng slot theo nguồn ở §5.9; `source_exam_doc_id = null` khi `item_ids` rỗng (duyệt 2026-10-26 chiều, §5.9). Với slot tính toán, chạy lại self-solve trong sandbox trên `params` đã duyệt để lấy `distractors` cho `distractor_archetypes` (I1: code chỉ so sánh).
7. Lưu blueprint kèm danh sách `item_ids` (để §6.3 kiểm lại I5), trả `201 ExamBlueprint` với header `Location: /v1/blueprints/{blueprint_id}`.

**Giới hạn:** ≤ 50 item và ≤ 50 slot lý thuyết mỗi blueprint. **Bất biến:** I5 (chỉ item đã duyệt, không non-cloneable); I1 (`distractor_archetypes` lấy từ sandbox).

#### `PATCH /v1/ingestion/items/{item_id}/labels` (đã duyệt 2026-10-25)

```json
{"editor_id": "local_user", "subject_code": "ET4010", "chapter_code": "ch3", "cognitive_level": "apply"}
```

Đặt các field có trong body vào `IngestionItem.labels`, rồi đặt `confirmed_by_human = true` và `mapping_confidence = 1.0`. Nhãn không ảnh hưởng số liệu hay Gate, nên được sửa cả sau khi item đã duyệt. Khi item được tạo và đã có cấu hình LLM, LLM đề xuất `chapter_code` và `cognitive_level` trên văn bản đề; lỗi hoặc không có LLM thì giữ mặc định (§5.4), không trả lỗi. **Response:** `200 IngestionItem`. **Lỗi:** 404, 422.

#### Đọc blueprint và batch (F18)

| Method | Path | Response | Lỗi |
|---|---|---|---|
| GET | `/v1/blueprints?subject_code=` | `200 list[BlueprintSummary]`, mới nhất trước; không phân trang (đề xuất) | 422 |
| GET | `/v1/blueprints/{blueprint_id}` | `200 ExamBlueprint` | 404 |
| GET | `/v1/blueprints/{blueprint_id}/twins` | `200 list[TwinBatchSummary]`, mới nhất trước | 404 (blueprint) |
| GET | `/v1/blueprints/{blueprint_id}/twins/{batch_id}` | `200 TwinBatch` | 404 (blueprint hoặc batch không có, hoặc batch thuộc blueprint khác) |

```python
class BlueprintSummary(StrictModel):             # đã triển khai (duyệt 2026-10-28)
    blueprint_id: str
    subject_code: str
    total_questions: int
    created_at: datetime
    batch_count: int                             # code: số batch đã sinh

class TwinBatch(BaseModel):                      # đã duyệt 2026-10-25: thêm batch_id và created_at vào model hiện có (§5.6)
    batch_id: str = Field(pattern=r"^tb_[0-9a-f]{16}$")   # code: sinh khi tạo batch; phát lại theo Idempotency-Key trả đúng id cũ
    blueprint_id: str
    base_seed: int
    twins: list[TwinQuestion]                    # đã triển khai: list[BatchTwin], union theo question_kind (§5.11, duyệt 2026-10-28)
    review_tickets: list[ReviewTicket]
    created_at: datetime

class TwinBatchSummary(StrictModel):             # đề xuất
    batch_id: str
    base_seed: int
    count: int                                   # TwinBatchRequest.count
    format: QuestionFormat                       # TwinBatchRequest.format
    twin_count: int                              # code: len(twins)
    review_ticket_count: int                     # code: len(review_tickets)
    created_at: datetime
```

- Các GET chỉ đọc kết quả đã lưu và không chạy sandbox. Twin trả về đã được sinh dưới I5 ở thời điểm `POST …/twins`.

### 6.8 Môn học và `exam_format` (§5.14) — đã triển khai (duyệt 2026-10-28)

Nhu cầu "mỗi môn có `exam_format`, kèm endpoint đọc và sửa" đã duyệt 2026-10-25; model `Subject` (§5.14) đã duyệt tối 2026-10-25; đường dẫn, body và mã lỗi dưới đây đã duyệt và triển khai (duyệt 2026-10-28, câu 1; `routers/subjects.py`). Mọi endpoint chạy offline. Chuỗi `detail` theo code: 404 `"không tìm thấy môn học"`; 409 khi tạo trùng `"môn học đã có"`; 409 khi `expected_version` lệch (nội dung do repository). `subject_code` thiếu `display_name` ⇒ dùng `subject_code`; `exam_format` mặc định `mixed`. `PATCH` không có cả `display_name` lẫn `exam_format` ⇒ 422.

| Method | Path | Request | Response | Lỗi |
|---|---|---|---|---|
| GET | `/v1/subjects` | — | `200 list[Subject]`, sắp theo `subject_code` | — |
| GET | `/v1/subjects/{subject_code}` | — | `200 Subject` | 404; 422 (`subject_code` sai mẫu) |
| POST | `/v1/subjects` | `{subject_code, display_name?, exam_format?}` | `201 Subject`, header `Location` | 409 (môn đã có, kể cả môn tạo ngầm); 422 |
| PATCH | `/v1/subjects/{subject_code}` | `{editor_id, expected_version, display_name?, exam_format?}`; ít nhất một trong hai field sửa | `200 Subject`, `version` tăng 1 | 404; 409 (version lệch); 422 |

- Đổi `exam_format` chỉ đổi mặc định cho blueprint tạo sau đó; blueprint và twin đã có giữ nguyên (§5.14).
- Không có `DELETE` trong MVP: xoá môn cần quyết định số phận của tài liệu, Vault và blueprint cùng môn.

### 6.9 Câu tự luận: sinh và chấm (§5.14) — MVP nếu còn thời gian (lịch 30/10–2/11, sau MVP-1), chưa triển khai

Contract đã duyệt 2026-10-25 ở mức field (§5.14); đường dẫn `POST /v1/twins/{twin_id}/essay-grade` đã duyệt tối 2026-10-25; mã lỗi là đề xuất (chờ duyệt). Cả hai bước chạy offline, không cần LLM. Khi khả năng `essay_grading_offline` không khả dụng (kể cả vì phần tự luận bị chuyển sang sau MVP), các endpoint dưới đây trả 409 `CapabilityError` (§6.0).

**Sinh câu.** Câu tự luận đi cùng đường với câu lý thuyết MCQ4, không có endpoint riêng, để mọi hình thức câu dùng chung blueprint, batch, I5 và R6:
1. `POST /v1/blueprints` (§6.7) với slot lý thuyết `format = "ESSAY"`, hoặc `format` null trong môn có `exam_format = essay`:

```json
{"subject_code": "PHI1006", "theory_slots": [{"chapter_code": "ch2", "cognitive_level": "understand", "format": "ESSAY"}]}
```

2. `POST /v1/blueprints/{blueprint_id}/twins` (§6.3), body `TwinBatchRequest` như hiện nay (`format` của request không ảnh hưởng slot lý thuyết). Response `201 TwinBatch`; mỗi twin của slot `ESSAY` có dạng (rút gọn):

```json
{
  "twin_id": "twin_…", "slot_index": 1, "format": "ESSAY", "question_kind": "theory",
  "stem": "Trình bày khái niệm «vật chất».",
  "options": null, "correct_key": null, "numerical_answer": null, "distractor_to_misconception_map": {},
  "essay": {
    "essay_kind": "define_concept", "pair_id": "cp_…", "term": "vật chất", "splitter_version": "1",
    "points": [
      {"point_index": 0, "text": "…đoạn nguyên văn thứ nhất của definition_quote…", "syllable_count": 14},
      {"point_index": 1, "text": "…đoạn nguyên văn thứ hai…", "syllable_count": 9}
    ]
  },
  "solution_explanation_vi": "…definition_quote nguyên văn…",
  "citation": {"doc_id": "doc_…", "page_number": 12, "verbatim_quote": "…definition_quote…", "chunk_id": "chk_…"},
  "trace": {"kind": "theory", "generator_version": "1", "pool_sha256": "…", "seed_rerolls": 0},
  "review_status": "AUTO_PASS"
}
```

   Response chứa sẵn các ý và đáp án mẫu, giống `correct_key` của câu MCQ4. Màn luyện tập PHẢI ẩn `essay.points`, `solution_explanation_vi` và `citation` cho tới khi người học nộp câu trả lời. Đây là app một người dùng để tự ôn, nên không cần giấu đáp án ở phía server.

**Chấm.** `POST /v1/twins/{twin_id}/essay-grade`

```python
class EssayGradeRequest(StrictModel):
    answer_text: str = Field(min_length=1)   # người học; độ dài tối đa kiểm theo cấu hình ESSAY_ANSWER_MAX_CHARS (mặc định 5000), không ghi cứng trong model; vượt ⇒ 422
```

- **Xử lý:** đọc twin đã lưu (§5.13 bảng `twins`; bản in-memory cần tra twin theo `twin_id`), kiểm `format = ESSAY`, chấm theo §5.14 trên `essay.points` đã lưu trong twin, không tách lại ý từ `definition_quote` hiện tại của cặp. Nhờ vậy sửa cặp khái niệm sau khi sinh câu không làm đổi kết quả chấm của twin cũ (trích dẫn là bản chụp, ADR-006 mục 7).
- **Response:** `200 EssayGradeResult` (§5.14). Không lưu câu trả lời hay kết quả (§5.13).
- **Lỗi:** 404 (twin); 422 (body sai, câu trả lời rỗng sau khi bỏ khoảng trắng, quá 5000 ký tự, hoặc twin không phải `ESSAY`); 409 `CapabilityError`.
- **Log** (§14): `twin_id`, `grader_version`, `total_points`, `matched_count`, độ dài câu trả lời; KHÔNG log văn bản câu trả lời.

### 6.10 Cây mục, khối, ảnh cắt và tra cứu khối (ADR-010) — contract, chưa triển khai

Phạm vi endpoint duyệt 2026-11-01 (ADR-010) (file quyết định mục 3: "endpoint đọc cây + tra cứu"); đường dẫn, tên model và tham số là đề xuất suy ra. Mọi endpoint chạy offline, chỉ đọc (GET), qua `LocalAccessMiddleware` như mọi request (§13.1). Model khối/mục ở §5.15. Frontend chỉ hiển thị field backend trả (thứ tự, số đếm, khoảng tô sáng), không tự tính (I1, §3.3). Model response mới ⇒ `src/types/holocron.ts` 1:1 (CLAUDE.md §2.3).

| Method | Path | Request | Response | Lỗi | Việc |
|---|---|---|---|---|---|
| GET | `/v1/vault/tree` | query `subject_code` (bắt buộc) | `200 SubjectTree` | 404 (môn không có), 422 | X09 |
| GET | `/v1/documents/{document_id}/sections/{section_id}/blocks` | query `scope` ∈ `eligible` (mặc định) \| `all`, `offset` ≥ 0 (mặc định 0), `limit` 1–500 (mặc định 200) | `200 SectionBlocks` | 404 (tài liệu, mục), 422 | X09 |
| GET | `/v1/documents/{document_id}/pages/{page_number}/blocks` | — | `200 PageBlockList` (mọi khối của trang, kể cả chưa đủ điều kiện; dùng cho màn xem lại) | 404 (tài liệu, trang ngoài `1..page_count`) | X09 |
| GET | `/v1/documents/{document_id}/crops/{crop_ref}` | `crop_ref` khớp `^[0-9a-f]{64}$` | `200` byte PNG (phục vụ khi tài liệu có ít nhất một hàng `crop_images` với `crop_ref` này; khoá file nội dung, §5.13) | 404 (tài liệu, không hàng nào của tài liệu này trỏ tới `crop_ref`, file mất), 422 (`crop_ref` sai mẫu) | X09 |
| GET | `/v1/vault/blocks/search` | query `q` (1–200 ký tự sau NFC và bỏ khoảng trắng đầu/cuối; ≤ 32 token sau gấp dấu), `subject_code` (bắt buộc), `document_id` (lặp được), `chapter_code` (lặp được), `type` (`BlockType`, lặp được; `header_footer`, `page_number` ⇒ 422), `offset` 0–1000 (mặc định 0), `limit` 1–50 (mặc định 20) | `200 BlockSearchPage` | 404 (môn không có), 422 | X10 |

```python
class SectionNode(StrictModel):
    section: Section                              # §5.15
    children: list[SectionNode]                   # code: theo ordinal

class SourceNode(StrictModel):                    # "Nguồn" = một tài liệu (ADR-010 mục 4)
    document_id: str; title: str; source_kind: SourceKind      # chép DocumentMeta (§5.8)
    chapter_code: str; chapter_title: str | None               # chép DocumentImport (§5.7)
    layout_version: int | None                    # chép DocumentImport
    chapter_shared_with: list[str]                # code: document_id khác cùng môn, cùng chapter_code, theo uploaded_at
    root: SectionNode | None                      # code: null khi tài liệu không có khối (layout_version null)
    # --- đếm trang không có khối, để không hụt im lặng (đề xuất suy ra (orchestrator); tên đề xuất suy ra). Code đếm; frontend chỉ hiển thị
    layout_failed_pages: list[int] = []           # code: page_number tăng dần của trang có LAYOUT_FAILED (nội dung chỉ ở chunk dòng)
    ocr_pages_without_blocks: list[int] = []      # code: page_number của trang OCR/bitmap chưa có khối (tới khi X11 làm)

class SubjectTree(StrictModel):                   # "Môn" (§5.14)
    subject_code: str; display_name: str          # chép Subject
    sources: list[SourceNode]
        # code: chỉ tài liệu ĐÃ ĐÓNG (status = "confirmed" hoặc cửa sổ settle-pending, §5.15), source_kind ≠ past_exam
        # (phạm vi Vault); sắp theo số chương
        # (ch2 < ch10), rồi uploaded_at, rồi document_id

class SectionBlocks(StrictModel):
    section: Section; total: int = Field(ge=0)    # code: số khối theo scope
    blocks: list[Block]                           # code: theo (trang, reading_order); scope = eligible ⇒ chỉ khối đủ điều kiện

class PageBlockList(StrictModel):                 # tên khác PageBlocks nội bộ của services/block_pipeline.py (§5.15)
    document_id: str; page_number: int
    layout_version: int | None                    # chép DocumentImport
    blocks: list[Block]                           # code: theo reading_order; rỗng khi trang chưa có khối
    ledger: ExtractionLedger | None               # code: null khi trang chưa có ledger (pending, failed, tài liệu cũ)

class MatchSpan(StrictModel):                     # khoảng khớp trong chữ GỐC (ADR-010 mục 18 (j''))
    target: Literal["text", "cell", "label"]      # code: Block.text; Block.cells[row][cell_index].text; Block.labels[label_index]
    row: int | None = None; cell_index: int | None = None     # code: chỉ target = "cell" (chỉ số trong Block.cells)
    label_index: int | None = None                # code: chỉ target = "label"
    start: int = Field(ge=0); end: int = Field(ge=1)
        # code: đơn vị UTF-16 code unit của chuỗi đích (frontend cắt bằng String.prototype.slice), start < end; backend
        # ánh xạ vị trí sau gấp dấu/NFD/trigram về chuỗi gốc

class SectionPathItem(StrictModel):
    section_id: str; title: str | None            # code: từ gốc (chương) tới Section chứa khối

class BlockSearchHit(StrictModel):
    block: Block                                  # loại, trang, bbox, crop_ref, flags, chữ
    document_title: str; chapter_code: str        # chép DocumentMeta.title, DocumentImport.chapter_code
    section_path: list[SectionPathItem]           # code
    match_kind: Literal["exact", "folded", "fuzzy"]
        # code: exact = token khớp nguyên dạng sau NFC, casefold và chuẩn hoá vị trí dấu thanh (ADR-006 mục 5 bước 2), giữ dấu
        # (gồm token kỹ thuật R13, CPSR, 0x1F);
        # folded = khớp sau gấp dấu; fuzzy = trigram hoặc khoảng cách sửa (token ngắn)
    match_spans: list[MatchSpan]                  # code: ≥ 1 phần tử, theo vị trí
    score: float                                  # code: chỉ để xếp hạng, UI không hiển thị

class BlockSearchPage(StrictModel):
    total: int = Field(ge=0, le=1000)             # code: số kết quả, trần 1000
    total_capped: bool                            # code: true khi số kết quả thật > 1000
    hits: list[BlockSearchHit]
    engine: Literal["fts5", "fts5_trigram", "python"]   # code: đường đã dùng (thông tin chẩn đoán)
    layout_failed_pages: int = Field(ge=0)        # code: số trang LAYOUT_FAILED trong phạm vi lọc (không tra cứu khối được)
    ocr_pages_without_blocks: int = Field(ge=0)   # code: số trang OCR/bitmap chưa có khối trong phạm vi lọc
        # hai số trên đếm trên tài liệu đã đóng (§5.15) khớp bộ lọc subject_code/document_id/chapter_code (đề xuất suy ra (orchestrator); tên
        # đề xuất suy ra); frontend hiện cảnh báo "có N trang chưa tra cứu theo khối được", không tự đếm
```

- **Tra cứu** (ADR-010 mục 18): chỉ khối đủ điều kiện (§5.15) của `subject_code`; truy vấn và chữ khối qua cùng hàm gấp dấu (NFC → `casefold` → NFD → bỏ dấu kết hợp → `đ` thành `d` → NFC; ADR-010 mục 18). Xếp hạng tất định: `match_kind` (exact > folded > fuzzy), rồi `score` giảm dần, rồi `document_id`, `page_number`, `reading_order`, `block_id`. Kết quả khớp qua `labels` của figure có cờ `figure_label_text` trong `block.flags`. Ký tự đặc biệt của FTS5 (`"`, `*`, `NEAR`, `:`) trong `q` không bao giờ đi thẳng vào câu `MATCH` (truy vấn dựng từ token đã gấp dấu, mỗi token đặt trong ngoặc kép). Mục tiêu p95 < 100 ms trên 3 chương đo bằng `scripts/eval_search.py`, không phải ngưỡng trong pytest. `GET /v1/vault/search` (§6.6) không đổi.
- **Ảnh cắt:** header `Content-Type: image/png`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=31536000, immutable`; body là đúng byte PNG đã kiểm (sha256 = `crop_ref`). Frontend tải qua `lib/api.ts` (gửi `X-Holocron-Token` khi có) → blob → object URL, thu hồi khi unmount; KHÔNG đặt token vào URL, KHÔNG `<img src>` trỏ thẳng endpoint.
- **Cây:** dựng từ khối đủ điều kiện (§5.15). `PATCH …/chapter` cập nhật `chapter_code` và `title` của Section gốc, không đổi `section_id` (§5.15). Cây và tra cứu chỉ có tài liệu đã đóng; sau khi đóng, `…/unconfirm`, `…/restore` trả 409 nên không thể làm khối rời tập đủ điều kiện.
- **Lỗi 422 của tra cứu khối:** `type` là `header_footer`/`page_number`, `q` rỗng sau khi bỏ khoảng trắng hoặc > 200 ký tự hoặc > 32 token, `offset` > 1000, `limit` ngoài 1–50.

## 7. Ingestion Gate

### 7.1 Trích xuất dual-pass
- Hai lượt PHẢI độc lập: khác prompt hệ thống (lượt B dùng prompt diễn đạt lại). Dual-pass KHÔNG đặt `temperature` (duyệt 2026-10-25): adapter không đặt `temperature` hay `top_p` tuỳ ý (§11.5), nên bỏ cách cũ "lượt A `temperature=0`, lượt B `temperature=0.4`". Mỗi ô có `confidence` ∈ [0, 1]. Dual-pass VLM đang park (2026-10-25, §11.5).
- Hai lượt PHẢI dùng cùng lưới `(row, col)`; ô một lượt không đọc được thì vắng mặt (sinh `MISSING_CELL` hoặc `DUAL_PASS_MISMATCH`).

### 7.2 Năm tín hiệu

| Tín hiệu | Mã issue | Điều kiện | Mức |
|---|---|---|---|
| S1 Dual-pass | `DUAL_PASS_MISMATCH` | Hai lượt khác nhau sau chuẩn hoá (trim, gộp khoảng trắng, lower) và ô chưa được con người xác nhận | BLOCKING |
| S2 Confidence | `LOW_CONFIDENCE` | `confidence < 0.85` và ô chưa xác nhận | WARNING |
| S3 Schema | `HEADER_MISMATCH` | Số cột sai (BLOCKING) / tên header không khớp alias (WARNING) | BLOCKING / WARNING |
| S3 Schema | `NOT_A_NUMBER`, `OUT_OF_RANGE`, `DUPLICATE_LABEL`, `MISSING_CELL` | Ô số không parse được (chấp nhận nhầm `O`→`0`); ngoài miền (arrival 0–200, burst 1–100); trùng tên tiến trình; thiếu ô | BLOCKING |
| S4 Text layer | `NOT_IN_TEXT_LAYER` | PDF có text layer nhưng số trong ô không xuất hiện trong text của trang | WARNING |
| S5 Self-solve | `SELF_SOLVE_MISMATCH` | Kết quả `solve()` trên bảng hiện tại khớp 0 hoặc ≥ 2 phương án (dung sai `max(tolerance_abs, 10^-decimals/2)`) | BLOCKING |
| Stem | `LATEX_UNBALANCED` | Số `$` không thoát bị lẻ, hoặc `{}` lệch | BLOCKING |
| S1' Cross-check (ADR-008 mục 11, chưa triển khai) | `CROSS_CHECK_UNAVAILABLE` | Item tạo từ trang PDF có `cross_check = "unavailable"`: mỗi ô số (cột `int`/`float` của `input_schema`) chưa được con người xác nhận | WARNING |

Đổi theo ADR-005 bước 1 (chưa triển khai): số cột, alias header, kiểu và miền giá trị của S3 lấy từ `input_schema` của template đã kiểm chứng (§5.10) thay cho `TABLE_SCHEMAS`; số dòng tối đa lấy từ `input_schema.tables[0].max_rows` thay cho `MAX_PROCESSES` (đã duyệt 2026-10-24); `DUPLICATE_LABEL` chỉ áp cho cột `unique` (đề xuất). Ví dụ "arrival 0–200, burst 1–100" ở bảng trên là miền của template RR.

Ngoài ra, bảng chưa chuẩn hoá được thành `CPUSchedulingProblem` (thiếu quantum, vi phạm miền…) sinh issue BLOCKING "Chưa thể tự giải" và `self_solve.status = SKIPPED`. Tương tự, `self_solve.status = SKIPPED` do sandbox lỗi/timeout/sai hợp đồng, hoặc topic chưa có template đã kiểm chứng, sinh issue BLOCKING dùng mã `SELF_SOLVE_MISMATCH` (không có mã mới) ⇒ không thể AUTO_PASS (I5). `POST /v1/ingestion/items` trả 422 với topic chưa hỗ trợ.

### 7.3 Trạng thái & hành động
- `BLOCKED` khi có ≥ 1 BLOCKING; `NEEDS_REVIEW` khi chỉ có WARNING; ngược lại `AUTO_PASS`.
- Approve được phép khi status khác `BLOCKED` và item không `non_cloneable` (I5). WARNING không chặn approve nhưng PHẢI hiển thị.
- Frontend: ô có issue BLOCKING PHẢI viền đỏ nhấp nháy (tắt animation khi `prefers-reduced-motion`, thay bằng viền nét đứt); ô WARNING viền cam; ô đã xác nhận viền xanh. Ô có `alternatives` PHẢI cho chọn nhanh một trong các giá trị.

### 7.4 Bảng từ bộ nhập PDF và đối chiếu chéo (ADR-008 mục 11) — chưa triển khai

Khi `IngestionItem` được tạo từ trang PDF đã xác nhận (§6.5), code dựng đầu vào của Gate như sau:

| Field của item | Nguồn |
|---|---|
| `source_doc_id`, `page_number` | `PageExtraction.document_id`, `PageExtraction.page_number` |
| `table_primary` | Bảng `table_id` trong `PageExtraction.tables`, lấy từ lượt đọc chính. `confidence` của ô là `conf` của từ tương ứng (Tesseract) hoặc `text_score` (lớp chữ). Ô nằm trên dòng người dùng đã sửa nhận `verified_by_human = true` (đề xuất) |
| `table_secondary` | Cùng lưới ô với `table_primary`, giá trị là token của lượt đọc thứ hai (RapidOCR) trong hộp toạ độ của ô, khi `cross_check.status` là `ok` hoặc `mismatch`; `null` khi `unavailable` hoặc `not_applicable` |
| `page_text_layer` | Văn bản các dòng khi `tier_used = "text_layer"`; ngược lại `null` |
| `cross_check` | `PageExtraction.cross_check.status` (đã duyệt 2026-10-25, §5.4); `"unavailable"` khi `page_source` khác `"text_layer"` mà thiếu trạng thái đối chiếu (validator đề xuất, suy ra từ quyết định (a), §5.4) |
| `page_source` | `PageExtraction.tier_used`; bắt buộc truyền với item đến từ PDF (quyết định (a), duyệt 2026-10-25, §5.4); kiểu `TierUsed` không có mặc định là đề xuất suy ra từ quyết định (a) |

Quy tắc:
- **Token lệch ⇒ `DUAL_PASS_MISMATCH`.** Vì `table_secondary` mang giá trị của lượt thứ hai, tín hiệu S1 sẵn có sinh `DUAL_PASS_MISMATCH` (BLOCKING) kèm `alternatives` là hai giá trị, không cần mã mới (ADR-008 mục 11). Ô đã được con người xác nhận được bỏ qua như hiện nay.
- **Không có lượt thứ hai ⇒ `CROSS_CHECK_UNAVAILABLE` (WARNING), và ô số không được AUTO_PASS.** Khi `cross_check = "unavailable"`, S1 im lặng vì `table_secondary = null`. Gate thêm một issue `CROSS_CHECK_UNAVAILABLE` cho mỗi ô số chưa được con người xác nhận (có `row`, `col` để tô ô). Vì có WARNING, trạng thái tối đa là `NEEDS_REVIEW`, không thể `AUTO_PASS`. Người dùng sửa hoặc xác nhận ô (PATCH §6.2 đặt `verified_by_human = true`) thì issue của ô đó hết. WARNING không chặn approve (§7.3).
- `cross_check = "not_applicable"` (trang đọc bằng lớp chữ) không sinh issue này; S4 `NOT_IN_TEXT_LAYER` vẫn áp dụng.
- Quy tắc chỉ áp cho item có `cross_check` khác `null`; `cross_check = null` giữ nghĩa cũ (item không đến từ PDF). Khi nối API bộ nhập tài liệu, item đến từ PDF luôn có `page_source` (quyết định (a), duyệt 2026-10-25), và `page_source` khác `"text_layer"` mà thiếu trạng thái đối chiếu thì được coi là `unavailable` (validator đề xuất, suy ra từ quyết định (a), §5.4), nên không lọt qua quy tắc này vì `null`. Item tạo qua §6.1 hoặc `POST /v1/ingestion/items` giữ nguyên hành vi; các test sẵn có của Gate (ví dụ `test_clean_table_has_no_issue`, `test_low_confidence_is_warning_only` trong `tests/test_gate_codes.py`, gọi `check_table` với `secondary = None`) PHẢI xanh mà không sửa.
- Test cưỡng chế: `test_numeric_token_mismatch_raises_dual_pass_mismatch` và `test_cross_check_unavailable_blocks_auto_pass` (`tests/test_pdf_ocr.py`, ADR-008 mục 14), thuộc dòng I5 ở CLAUDE.md §9.

## 8. Executable Template Contract

### 8.1 Entry points (top-level, bắt buộc)

```python
def generate_params(seed: int) -> Params           # chỉ dùng random.Random(seed)
def validate(params: Params) -> None               # chỉ gồm assert có nhãn
def solve(params: Params) -> dict                  # {"value", "display", "explanation_vi", "exact"?, "gantt"?}
def make_distractors(params: Params, solution: dict) -> list[dict]   # đúng 3 phần tử
def render_stem(params: Params) -> str             # Markdown + LaTeX
```

- Với topic CPU Scheduling, `params` có dạng `CPUSchedulingProblem.to_template_params()` và `solve()` PHẢI trả thêm `"gantt": [{"process": str, "start": int, "end": int}, …]` (bổ sung T2-N3).
- Mỗi distractor là `{"misconception_code", "value", "display", "rationale_vi"}`; `misconception_code` PHẢI thuộc 3 mã khai báo trong `QuestionTemplate.misconceptions`.
- Template chạy trong sandbox nên chỉ được import whitelist (§9.1) và chỉ dùng kiểu builtin trong annotation.
- Đổi theo ADR-005 bước 3 (chưa triển khai): khoá `"gantt"` thay bằng `"artifacts": [{"kind": "timeline" | "table", …}, …]` đúng dạng `SolutionArtifact` (§5.11); template khai báo `required_artifacts` và `input_schema` trong file `.json` đi kèm (§5.10). Sandbox kiểm kiểu artifact theo `kind`.

**Đáp án nhiều giá trị (R10) — đã chọn (c) cho MCQ4 ngày 2026-10-25; (a) để sau MVP qua một ADR riêng.** Câu Vi xử lý hỏi giá trị thanh ghi và bốn cờ N, Z, C, V của CPSR sau một đoạn lệnh, trong khi hợp đồng hiện tại chỉ có một `value` số. Ba phương án:

| | (a) `answer` đa hình: `numeric \| record` | (b) Mỗi câu hỏi một giá trị | (c) Mã hoá thành một số |
|---|---|---|---|
| Cách làm | `solve()` trả `"value"` là số hoặc bản ghi các giá trị có tên (`{"R0": 31, "N": 0, "Z": 0, "C": 1, "V": 0}`); `AnswerSpec` và `NumericalAnswer` thành discriminated union theo `kind` | `generate_params()` chọn một đích hỏi (một thanh ghi, hoặc một cờ); một đoạn lệnh sinh nhiều câu qua nhiều seed | `value` là số nguyên mã hoá, ví dụ `NZCV = N·8 + Z·4 + C·2 + V`, hoặc gộp cả thanh ghi; `display` là chuỗi dễ đọc do sandbox sinh (`"N=0 Z=0 C=1 V=0"`) |
| I1 | Giữ: mọi giá trị của bản ghi do sandbox tính | Giữ | Giữ |
| I2 | Giữ: mỗi phương án nhiễu là một bản ghi đầy đủ ứng với đúng một misconception | MCQ4 hỏi một cờ chỉ có 2 giá trị nên không đủ 4 phương án khác nhau; câu về cờ chỉ làm được dạng NUMERICAL, tức không có phương án nhiễu | Giữ: mỗi phương án nhiễu là một tổ hợp cờ ứng với một misconception (ví dụ hiểu sai cờ C khi trừ) |
| I4 | Giữ; cần quy tắc chính tắc khi serialize bản ghi để so tái lập và dấu vân tay R6 | Giữ | Giữ |
| Gate S5 (self-solve) | Phải so từng trường; cần cách đọc bản ghi từ văn bản phương án của đề gốc | Không đổi | `_first_number` lấy số đầu tiên của phương án (`"N=0 …"` cho ra 0), nên self-solve đối chiếu sai; cần cách so theo `display` hoặc template tự đọc phương án |
| `DISTINCT_*`, `min_option_gap` | Cần định nghĩa khoảng cách giữa hai bản ghi | Không đổi | Khoảng cách số không có nghĩa, chỉ dùng được như "khác nhau" |
| Frontend | Thêm cách hiển thị bản ghi và ô nhập nhiều giá trị cho NUMERICAL | Không đổi | Không đổi; dạng NUMERICAL bắt người học nhập số mã hoá, nên chỉ nên dùng MCQ4 |
| Thay đổi contract | Lớn: `AnswerSpec`, `NumericalAnswer`, `TwinQuestion`, `types/holocron.ts`; cần ADR | Không | Nhỏ (chỉ cách so ở S5) |

**Quyết định (đã duyệt 2026-10-25):** (c) cho MVP, giới hạn ở MCQ4, vì giữ nguyên I1, I2, I4 và frontend, không cần ADR, và không làm chậm ADR-005 bước 1–3. Câu Vi xử lý có đáp án mã hoá KHÔNG ĐƯỢC sinh ở dạng NUMERICAL. Sau MVP chuyển sang (a) qua một ADR riêng nếu cần câu NUMERICAL nhiều giá trị. (b) bị loại vì không đạt mục tiêu "giá trị thanh ghi và cờ" ở §1.5 cho MCQ4.

Hệ quả của (c), đề xuất (chờ duyệt):
- **Cách so ở S5:** `AnswerSpec` thêm `match_mode: Literal["numeric", "display"] = "numeric"`. Với `"display"`, self-solve so `solve()["display"]` với văn bản từng phương án của đề gốc sau chuẩn hoá NFC, gộp khoảng trắng và `casefold`, thay vì lấy số đầu tiên (`_first_number`). Template Vi xử lý khai báo `"display"` trong file `.json` (§5.10); code không rẽ nhánh theo topic. Cùng cách so dùng cho `distractor_archetypes` (§5.9).
- **`min_option_gap`:** đặt `1` cho đáp án mã hoá nguyên, nên "cách nhau ≥ 1" đúng nghĩa là "khác nhau"; assert `DISTINCT_*` của template vẫn bắt buộc.
- **Dấu vân tay R6 và I4** không đổi, vì `params` và `value` vẫn là kiểu builtin.

### 8.2 Quy tắc assert
- Mỗi `assert` PHẢI có nhãn thuộc một trong bốn nhóm: `VALIDITY_*`, `TIE_*`, `NONDEG_*`, `DISTINCT_*`.
- `TIE_*` loại trường hợp đáp án phụ thuộc quy ước. RR: tiến trình đến đúng lúc tiến trình khác hết quantum (`TIE_ARRIVAL_AT_PREEMPTION`). SJF: hai tiến trình sẵn sàng có cùng burst (`TIE_EQUAL_BURST_READY`).
- `DISTINCT_*` đảm bảo 4 giá trị sau làm tròn đôi một cách nhau ≥ `min_option_gap`.

### 8.3 Số học
- Mọi phép chia dùng `fractions.Fraction`; chỉ làm tròn ở bước hiển thị (`decimals`).

### 8.4 Template chuẩn
- RR: `infrastructure/templates/os_rr_avg_waiting.py` — misconception `RR_TREATED_AS_FCFS`, `WAIT_EQUALS_FIRST_RESPONSE`, `WAIT_CONFUSED_WITH_TURNAROUND`.
- SJF non-preemptive (T2-N3): `infrastructure/templates/os_sjf_avg_waiting.py` — misconception `SJF_IGNORES_ARRIVAL`, `SJF_COMPUTED_AS_SRTF`, `WAIT_CONFUSED_WITH_TURNAROUND`.
- Mỗi template PHẢI có bộ giải tham chiếu độc lập viết trong test và property test ≥ 100 seed (§15).

## 9. Sandbox Runtime

### 9.1 Phòng thủ nhiều lớp

| Lớp | Cơ chế | Chặn |
|---|---|---|
| L1 ASTGuard | Whitelist import `random, math, fractions, itertools, functools, collections, statistics`; cấm builtins nguy hiểm, thuộc tính `_*`, `gi_/cr_/ag_/f_/tb_/co_*`, `.format/.format_map/.mro`; cấm `class/try/with/raise/global/delete/async`; số mũ hằng 0–64; literal ≤ 2 000 ký tự; ≤ 6 000 node; mã ≤ 20 000 byte | `os`, `sys`, `subprocess`, socket, file I/O, introspection escape |
| L2 Runtime | Tiến trình con `python -S -s -P`; builtins rút gọn; `__import__` chỉ trả proxy module đã nạp sẵn, không lộ module con | Escape qua module graph |
| L3 OS limits | Unix: `RLIMIT_AS` 256 MiB, `RLIMIT_CPU` 2 s, `RLIMIT_FSIZE` 0, `RLIMIT_CORE` 0. Windows: Job Object giới hạn bộ nhớ tiến trình + CPU user-time, `KILL_ON_JOB_CLOSE`. Cả hai: wall-clock ≤ 2 s ở tiến trình cha, env tối thiểu (`PYTHONHASHSEED=0`, `PYTHONIOENCODING=utf-8`, `SYSTEMROOT` trên Windows), cwd tạm | Vòng lặp vô hạn, memory bomb, ghi file |
| L4 (Tuần 4, đóng gói desktop) | nsjail/seccomp trên Linux; AppContainer trên Windows; không mạng | Phần còn lại |

`resource`/`RLIMIT_*` chỉ được dùng sau kiểm tra `sys.platform != "win32"`. Dòng `exec` duy nhất trong `_sandbox_child.py` mang `# noqa: S102` đã được duyệt.

### 9.2 Kết quả
`ok | ast_rejected | assertion_failed | runtime_error | contract_error | timeout | memory_exceeded | crashed`. Chỉ `assertion_failed` là lỗi của bộ số (re-roll seed); còn lại là lỗi của mã.

### 9.3 Hiệu năng
Mỗi lần chạy spawn một tiến trình (~40–60 ms Linux, ~120–150 ms Windows). Tuần 3 NÊN dùng pool tiến trình dựng sẵn và chạy song song các seed để đạt G3.

## 10. Retry Budget & Fallback

| Ngân sách | Giá trị | Bị trừ khi |
|---|---|---|
| Seed re-roll / template | 40 | `assertion_failed` (không gọi LLM) |
| Lượt LLM / slot | 3 | lỗi mã; 0/40 seed hợp lệ (`ill_posed`); dựng `TwinQuestion` thất bại (`twin_assembly_failed`, ví dụ misconception lạ) |

Cây quyết định:
1. Slot `non_cloneable` ⇒ `ReviewTicket` ngay.
2. ≤ 3 lượt LLM; mỗi lượt re-roll ≤ 40 seed theo dãy tất định `(seed_k + j·7919) mod SEED_SPACE`, `j ∈ [0, 40)`, trong đó `seed_k` là seed gốc của twin k (§6.3, R6 phương án B). Câu trùng dấu vân tay với twin đã sinh cũng re-roll trong dải này, không tính là `ill_posed` (§6.3).
3. Hết lượt ⇒ template `precomputed_verified` cùng `topic_key` ⇒ twin `AUTO_FALLBACK`. Fallback cũng dựng twin lỗi ⇒ `ReviewTicket`, không crash.
4. Không có fallback ⇒ `ReviewTicket`, slot `NEEDS_REVIEW`.

Feedback gửi LLM (một dòng mỗi lỗi): `AST_REJECTED: dòng N: …`, `RUNTIME_ERROR (stage=…, line=…): …`, `ILL_POSED: 0/40 seed … Assert hay vi phạm nhất: …`, `TWIN_ASSEMBLY_FAILED: …`, `SCHEMA_INVALID: …`. LLM PHẢI được yêu cầu nới miền sinh biến, KHÔNG ĐƯỢC xoá assert.

### 10.1 Kiểm chứng template ứng viên do LLM viết (§17 D6) — duyệt 2026-10-26, kèm điều kiện

> **Duyệt 2026-10-26 (K1–K7), kèm điều kiện:** có ít nhất 3 ví dụ có đáp án trích từ tài liệu làm đối chứng độc lập (độc lập với bộ giải tham chiếu do LLM viết). Điều kiện này trả lời câu hỏi mở thứ nhất bên dưới. Cách lấy và đối chiếu ba ví dụ đó chưa được mô tả; chờ duyệt khi làm luồng câu tính toán (§16). Ba câu hỏi mở còn lại vẫn chờ người dùng. Mục này suy ra từ CLAUDE.md §2.1 (hai bậc *ứng viên* / *đã kiểm chứng*), §2.3 (quy tắc template chạy trong sandbox) và §9 (test tái lập I4 và property test ≥ 100 seed tương đương `tests/test_rr_template.py`), áp cho template mà `TemplateAuthor` viết lúc chạy (bảng `templates`, §5.13). Mọi số ở đây lấy từ các nguồn đó hoặc từ §15; không có ngưỡng mới.

Template ứng viên gồm mã template và **bộ giải tham chiếu** do LLM viết trong cùng lượt (CLAUDE.md §2.1). Template ứng viên lưu với `tier = "candidate"`; chỉ dùng để kiểm chứng và xem trước có nhãn "chưa duyệt", không sinh câu hỏi phát hành, không làm fallback. Nó lên `tier = "verified"` khi qua lần lượt các lớp sau; trượt một lớp thì dừng, ghi lý do vào `verification_json` và, nếu còn lượt, gửi feedback cho LLM theo §10.

| Lớp | Kiểm gì | Nguồn quy tắc |
|---|---|---|
| K1 Schema | Đầu ra parse được thành `QuestionTemplate` (§11.3), có `input_schema`, `required_artifacts`, đúng 3 `misconceptions`, `assertions` có nhãn thuộc 4 nhóm (§8.2) | §5.10, §8.2, §11.3 |
| K2 ASTGuard | Cả mã template **và** bộ giải tham chiếu qua ASTGuard: chỉ import whitelist (`random, math, fractions, itertools, functools, collections, statistics`), annotation chỉ dùng kiểu builtin (không `typing`, không tên `object`), không `class/try/with/raise` | CLAUDE.md §2.3; §9.1 của tài liệu này |
| K3 Hợp đồng sandbox | Đủ 5 entry point (§8.1); chạy trong sandbox; `solve()` trả đủ artifact theo `kind` đã khai báo; `make_distractors()` trả đúng 3 phương án mang 3 mã đã khai báo (I2); bộ giải tham chiếu cũng chỉ chạy trong sandbox | §8.1, §9, I1, I2 |
| K4 Tỉ lệ chấp nhận seed | Trên ≥ 100 seed, tỉ lệ seed qua `validate()` ≥ 10 %; không seed nào cho `timeout`, `memory_exceeded`, `crashed` | §15 ("Done" cho topic mới), `RELEASE_CHECKLIST.md` mục 6 |
| K5 Property test | Trên ≥ 100 seed hợp lệ, `solve()["value"]` của template khớp kết quả bộ giải tham chiếu trong dung sai `AnswerSpec`; 4 phương án đôi một cách nhau ≥ `min_option_gap`; `build_twin` dựng được `TwinQuestion` hợp lệ (validator I2) cho mọi seed đó | CLAUDE.md §9 (tương đương `tests/test_rr_template.py`), §8.2 |
| K6 Tái lập I4 | Chạy lại cùng `(template_sha256, seed)` trên cùng tập seed cho đầu ra giống từng byte (trừ `twin_id`, `generated_at`, `sandbox_duration_ms`) | I4, G4 |
| K7 Người duyệt | Người dùng xem kết quả K1–K6 và vài câu xem trước có nhãn "chưa duyệt", rồi duyệt; code ghi `approved_by`, `approved_at` | CLAUDE.md §2.1 |

Câu hỏi mở của bản nháp:
- (Đã trả lời 2026-10-26 bằng điều kiện duyệt ở đầu mục: ≥ 3 ví dụ có đáp án trích từ tài liệu làm đối chứng độc lập.) K5 so template với bộ giải tham chiếu **cùng do LLM viết**, nên hai bên có thể cùng sai một cách; CLAUDE.md §9 dùng bộ giải tham chiếu viết tay trong test. Có cần người dùng xem bộ giải tham chiếu ở K7 không, hay cần một tiêu chí độc lập khác?
- Đổi `generator_source` (sha256 khác) thì template phải qua lại K1–K7 từ đầu (đề xuất).
- Template `verified` trong DB có được làm fallback không: §5.13 hiện giữ fallback `precomputed_verified` chỉ từ `infrastructure/templates/` của repo, trong khi CLAUDE.md §2.1 nói "chỉ template đã kiểm chứng được dùng ... làm fallback" mà không phân biệt nguồn.
- Template viết lúc chạy không vào `infrastructure/templates/`, nên K4–K6 là bộ kiểm chạy trong app, không phải `pytest`; muốn đưa vào repo thì vẫn cần test theo CLAUDE.md §9.

## 11. LLM Gate & cấu hình BYOK

### 11.1 LLM được và không được làm gì

| Việc | LLM | Ghi chú |
|---|---|---|
| Đọc bảng/stem từ ảnh (VLM) | Được — output không tin cậy | Luôn qua Ingestion Gate |
| Viết mã Executable Template | Được — output không tin cậy | Luôn qua ASTGuard + sandbox + validator |
| Phân loại `topic_key`, gợi ý archetype phương án nhiễu | Được | Chỉ là nhãn, không phải số |
| Đề xuất nhãn chunk, `chapter_code`, `cognitive_level`, cặp khái niệm (§5.4, §5.8) | Được — output không tin cậy | Người dùng xác nhận; trích dẫn qua kiểm nguyên văn I3 |
| Chấm câu trả lời tự luận (§5.14) | Chỉ khi có `AnswerJudge` (sau MVP): trả verdict từng ý kèm bằng chứng | Không có LLM thì chấm offline bằng n-gram. Điểm và kết luận luôn do code tính (CLAUDE.md §2.1) |
| Tính bất kỳ con số nào hiển thị cho người dùng | **KHÔNG ĐƯỢC** | I1 |
| Viết lời giải chứa số | **KHÔNG ĐƯỢC** | Lời giải là `explanation_vi` do `solve()` sinh |

Mọi việc của LLM ở bảng trên là **tuỳ chọn** (đã duyệt 2026-10-25): thiếu LLM thì việc đó báo không khả dụng (§11.4) hoặc có cách làm offline thay thế, phần còn lại của app vẫn chạy.

### 11.2 Cấu hình (module `infrastructure/llm_config.py`, T2-N3)

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `HOLOCRON_LLM_BASE_URL` | — (không có; đổi 2026-10-25) | Địa chỉ gốc kiểu OpenAI, **gồm cả phần version**, giống `base_url` của OpenAI SDK; adapter nối thêm `/chat/completions` (quy ước đã duyệt 2026-10-25). Ví dụ: `https://<gateway>/v1`; một gateway hay model local tương thích OpenAI cũng dùng được, chỉ là ví dụ, không phải mặc định |
| `HOLOCRON_LLM_API_KEY` | — | Key người dùng cấu hình (hiện là key ban tổ chức cấp); KHÔNG ĐƯỢC ghi log, KHÔNG ĐƯỢC trả về client |
| `HOLOCRON_LLM_MODEL_EXTRACT` | — (không có; đổi 2026-10-25) | Model cho đề xuất nhãn và cặp khái niệm. Ví dụ hiện dùng: `claude-haiku-5-5` qua gateway của ban tổ chức |
| `HOLOCRON_LLM_MODEL_AUTHOR` | = `HOLOCRON_LLM_MODEL_EXTRACT` (mặc định này duyệt 2026-10-26; §17 D7) | Model viết template |
| `HOLOCRON_VLM_BASE_URL` / `HOLOCRON_VLM_API_KEY` / `HOLOCRON_VLM_MODEL` | — | Model **có thị giác** cho dual-pass và tầng 4 (tương thích OpenAI, nhận `image_url`). Đang park: chưa kiểm gateway hiện có nhận ảnh hay không (R4) |
| `HOLOCRON_LLM_TIMEOUT_S` | `60` | Timeout mỗi request |
| `HOLOCRON_REQUIRE_TOKEN` | — (không yêu cầu token) | Đã duyệt 2026-10-25 (§13.1): đặt biến này thì mọi request PHẢI mang token trong header `X-Holocron-Token`; giá trị của biến chính là token, do Tauri sinh ngẫu nhiên mỗi lần khởi động và truyền cho sidecar (duyệt 2026-10-25). Độ dài và mã hoá (≥ 32 byte, base64url) là đề xuất. KHÔNG ĐƯỢC ghi log. Duyệt 2026-10-29 (câu 6), thay quyết định câu 21 (a) ngày 2026-10-28: không đặt hoặc `""` ⇒ không yêu cầu token; có giá trị mà toàn khoảng trắng (mọi khoảng trắng Unicode) hoặc không hợp lệ theo `is_valid_token` (ký tự ngoài ASCII in được kể cả NBSP, U+3000, `\n`, `\r`, ký tự điều khiển, hoặc khoảng trắng đầu/cuối) ⇒ cấu hình hỏng: app từ chối khởi động (lifespan và launcher; mã thoát tuỳ đường chạy, §13.1), thông điệp chỉ nêu tên biến, không nêu giá trị; lúc chạy middleware vẫn 403 fail-closed như lớp thứ hai (§6.0, §13.1) |
| `HOLOCRON_PDF_OCR_PRIMARY` | `tesseract` | Engine chính của tầng 2. MVP chỉ nhận `tesseract` (đã duyệt 2026-10-24; ADR-008 mục 12); giá trị khác là `ConfigError` |
| `HOLOCRON_PDF_OCR_SECONDARY` | `rapidocr` | Lượt đọc thứ hai (model PP-OCRv6 small đóng sẵn trong wheel, đã duyệt 2026-10-24). Rỗng nghĩa là không có lượt thứ hai (`cross_check = unavailable`). Không được cùng họ model với lượt chính |
| `HOLOCRON_TESSERACT_CMD` | `tesseract` (tìm trong PATH) | Đường dẫn binary Tesseract do người dùng tự cài. Đường dẫn tương đối (không có gốc) được đổi sang tuyệt đối theo thư mục làm việc của app lúc đọc cấu hình; đường dẫn đã có gốc được giữ nguyên: dạng `/x` và dạng có ổ đĩa (`C:/x`, `C:\x`) luôn coi là có gốc, còn dạng `\x` chỉ coi là có gốc trên Windows (nơi `\` là dấu phân cách); trên Linux/macOS `\x` là đường dẫn tương đối và được đổi sang tuyệt đối theo thư mục làm việc của app lúc đọc cấu hình; tên lệnh trần (vd. `tesseract`) thì tra PATH. Giới hạn đã biết (nợ): trên Windows, dạng gốc không ổ (`/x`, `\x`) được hiểu theo ổ hiện tại của từng tiến trình, nên có thể lệch giữa kiểm khởi động và worker khi app và thư mục tạm ở hai ổ khác nhau; nên dùng đường dẫn có ổ đĩa. Lý do: lần kiểm lúc khởi động và worker (chạy ở thư mục tạm) phải thấy cùng một đường dẫn (duyệt 2026-10-30, quyết định Tessdata 5.x: không lệch giữa kiểm khởi động và worker) |
| `HOLOCRON_TESSDATA_DIR` | thư mục mặc định của bản cài Tesseract | Thư mục chứa `vie.traineddata`, truyền qua `--tessdata-dir`. Không đặt thì không truyền cờ; Tesseract 5.x dùng thư mục của chính nó. Đường dẫn tương đối (không có gốc) được đổi sang tuyệt đối theo thư mục làm việc của app lúc đọc cấu hình; đường dẫn đã có gốc được giữ nguyên: dạng `/x` và dạng có ổ đĩa (`C:/x`, `C:\x`) luôn coi là có gốc, còn dạng `\x` chỉ coi là có gốc trên Windows (nơi `\` là dấu phân cách); trên Linux/macOS `\x` là đường dẫn tương đối và được đổi sang tuyệt đối theo thư mục làm việc của app lúc đọc cấu hình. Giới hạn đã biết (nợ): trên Windows, dạng gốc không ổ (`/x`, `\x`) được hiểu theo ổ hiện tại của từng tiến trình, nên có thể lệch giữa kiểm khởi động và worker khi app và thư mục tạm ở hai ổ khác nhau; nên dùng đường dẫn có ổ đĩa. Mục đích: lần kiểm lúc khởi động và worker (chạy ở thư mục tạm) thấy cùng một đường dẫn (duyệt 2026-10-30, quyết định Tessdata 5.x: không lệch giữa kiểm khởi động và worker). `TESSDATA_PREFIX` không được app đọc và không được truyền cho Tesseract (duyệt 2026-10-30, §11.4) |
| `HOLOCRON_DATA_DIR` | — (không đặt ⇒ kho in-memory, duyệt 2026-10-25; file PDF gốc ghi tạm vào thư mục tạm của hệ điều hành, xoá khi tắt app, duyệt 2026-10-26 chiều, §5.13) | Đã duyệt 2026-10-25 (§5.13): thư mục chứa `holocron.sqlite3` và file gốc. Bản đóng gói (zip MVP-1, sau này Tauri) luôn đặt biến này qua launcher (§1.5) |
| `HOLOCRON_LOG_DIR` | — (không đặt ⇒ `RuntimeInfo.log_dir = null`) | Duyệt 2026-10-28 (§5.12, §14). Do launcher quản lý, người dùng KHÔNG đặt tay: launcher đặt biến này (đường dẫn thư mục log) khi mở được file log, và xoá biến khỏi môi trường tiến trình con khi không mở được. `GET /v1/runtime` đọc biến này cho `log_dir`. Không có trong `.env.example` |
| `HOLOCRON_LLM_DATA_RECIPIENT` | — (không đặt ⇒ `RuntimeInfo.llm_data_recipient = null`) | Hướng duyệt 2026-10-26 chiều; tên biến và quy tắc là đề xuất (chi tiết), chờ duyệt (§5.12): nhãn do người dùng đặt cho bên nhận dữ liệu gửi LLM, hiển thị trên UI (§13.1). Tối đa 80 ký tự; KHÔNG ĐƯỢC chứa URL, key hay tên model (giá trị vi phạm bị bỏ qua). Chưa có trong `.env.example` (file này sửa ở commit riêng sau khi PR triển khai merge) |
| `HOLOCRON_API_URL` | `http://127.0.0.1:8000` | Biến của frontend (`next.config.ts`, đã triển khai): địa chỉ backend cho rewrite `/api/holocron/*` |
| `HOLOCRON_STATIC_EXPORT` | — (không đặt ⇒ build thường, có rewrite) | Tên duyệt 2026-10-27 (Q8). Cờ build của frontend, không phải biến chạy của backend: `next.config.ts` chỉ bật `output: "export"` và `trailingSlash: true` (bỏ rewrite) khi giá trị đúng bằng `1`. `npm run build:static` (`scripts/build-static.mjs`) đặt cờ này cùng `NEXT_PUBLIC_HOLOCRON_API_BASE=/` và `NEXT_PUBLIC_HOLOCRON_MOCKS` rỗng. Đã triển khai |
| `HOLOCRON_WEB_DIR` | — (không đặt ⇒ backend không phục vụ web, hành vi như cũ) | Tên duyệt 2026-10-27 (Q8). Đã triển khai. Đặt ⇒ backend phục vụ thư mục export của frontend ở `/` (§6.0, "Chế độ portable"); đường dẫn tương đối tính theo thư mục làm việc hiện tại. Thư mục không tồn tại hoặc không có `index.html` đọc được ⇒ `ConfigError` lúc tạo app, thông điệp chỉ nêu tên biến, không nêu đường dẫn. Rỗng hoặc toàn khoảng trắng coi như chưa đặt |

- Tên model và địa chỉ endpoint là cấu hình, KHÔNG ĐƯỢC hard-code trong service. **Không có nhà cung cấp mặc định** (đã duyệt 2026-10-25): bỏ hai mặc định (địa chỉ endpoint và tên model) của nhà cung cấp mặc định cũ trong bản v0.5. Nhà cung cấp do cấu hình quyết định (hiện là gateway kiểu OpenAI của ban tổ chức, model `claude-haiku-5-5`). Contract hiện chỉ có một adapter kiểu OpenAI Chat Completions sau `Protocol` `ChatCompletionClient` (§11.5); một gateway hay server local tương thích OpenAI dùng được qua adapter này chỉ bằng cấu hình. Anthropic gốc (API không theo giao thức OpenAI) cần một adapter riêng cài cùng `Protocol`, chưa có. Code (`infrastructure/llm_config.py`, `LLM_REQUIRED_VARIABLES`) đã khớp: không có giá trị mặc định cho địa chỉ, key hay model; LLM chỉ bật khi người dùng đặt tường minh đủ `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY` và `HOLOCRON_LLM_MODEL_EXTRACT`. Các assert mặc định cũ trong `tests/test_llm_config.py` đã được sửa theo đồng ý của người dùng (duyệt 2026-10-25, CLAUDE.md §7, §17 D12).
- **LLM được coi là đã cấu hình** (đã duyệt 2026-10-25) khi đủ `HOLOCRON_LLM_BASE_URL`, `HOLOCRON_LLM_API_KEY` và `HOLOCRON_LLM_MODEL_EXTRACT`. Thiếu biến nào thì các khả năng cần LLM báo `available = false`, `reason = "llm_not_configured"`, `detail_vi` là câu cố định nêu tên đủ ba biến bắt buộc, không nêu giá trị (duyệt 2026-10-27; §5.12, §11.4), không phải lỗi khởi động; endpoint cần LLM trả 409 `CapabilityError`, không gọi mạng (duyệt 2026-10-27). Model local không cần key là việc sau MVP (§1.6).
- Ngưỡng của bộ nhập (`TEXT_LAYER_MIN=0.85`, `TEXT_SPARSE_CHARS=32`, `TEXT_SPARSE_IMAGE_MIN=0.40` (thêm 2026-10-29, người dùng duyệt), `TEXT_FULL_IMAGE_MIN=0.90`, `TEXT_FULL_IMAGE_MAX_CHARS=200` (thêm 2026-10-30, người dùng duyệt; điều kiện (d), ADR-008 mục 6), `TEMPLATE_MIN_PAGE_RATIO=0.60` (đổi tên 2026-10-31 từ `TEMPLATE_IMAGE_MIN_PAGE_RATIO`; người dùng duyệt giá trị cuối sau phép đo GĐ0, §5.7), `TEMPLATE_IMAGE_MIN_PAGES=3`, `TEMPLATE_IMAGE_BBOX_STEP=0.01`, `TEMPLATE_SCAN_TIMEOUT_S=20.0`, `TEMPLATE_SCAN_MAX_IMAGES_PER_PAGE=256`, `TEMPLATE_SCAN_MAX_IMAGES=20000`, `TEMPLATE_SCAN_MAX_IMAGE_BYTES=16 MiB`, `TEMPLATE_SCAN_MAX_BYTES=512 MiB`, `FIGURE_IMAGE_AREA_MIN=0.10`, `FIGURE_PATH_AREA_MIN=0.25` (thêm 2026-10-30, người dùng duyệt; ảnh template, ADR-008 mục 6, 9, 12; tên field trong code viết thường), các ngưỡng GĐ0 ở bảng "Ngưỡng GĐ0" của §5.7 (thêm 2026-10-31), `TEXT_SCORE_MIN`, `OCR_SCORE_MIN`, `LINE_CONF_MIN`, `COVERAGE_MIN`, `FIGURE_INK_MIN`, `CROSS_LINE_CER_WARN`) và danh sách `OCR_CHARSET_ALLOWED_MISSING` (mặc định `Ỵ`, `ỵ`, §5.7) là field có giá trị mặc định của model cấu hình ở `infrastructure/`, không phải biến môi trường (ADR-008 mục 12; tên `TEXT_LAYER_MIN` đã duyệt 2026-10-24).
- Biến đặt giá trị rỗng hoặc toàn khoảng trắng coi như chưa đặt (riêng `HOLOCRON_REQUIRE_TOKEN`, duyệt 2026-10-29 (câu 6), thay quyết định câu 21 (a) ngày 2026-10-28: chỉ không đặt hoặc `""` mới coi như chưa đặt; giá trị toàn khoảng trắng là cấu hình hỏng, app từ chối khởi động, xem bảng trên); `HOLOCRON_LLM_TIMEOUT_S` không phải số dương hữu hạn là lỗi cấu hình (`ConfigError`, thông điệp chỉ nêu tên biến). Mẫu tất cả các biến ở `.env.example` (gốc repo; chỉ placeholder). Backend không tự đọc file `.env`; người dùng nạp biến vào shell trước khi chạy uvicorn.
- **Code hiện tại (`infrastructure/llm_config.py`):** khi thiếu cấu hình VLM (cần đủ ba biến `HOLOCRON_VLM_*`), hệ thống dùng `MockDualPassExtractor` và `GET /v1/runtime` trả `demo_mode = true`.
- **Contract mới (định nghĩa đã duyệt 2026-10-23; ghi vào contract 2026-10-24, §5.12; code đổi cùng F17):** theo G5 (§1.5), thiếu VLM không còn là chế độ demo vì bộ nhập PDF vẫn chạy đủ luồng bằng tầng 1–3. `demo_mode` = "thiếu LLM" (`llm_configured = false`; v0.5 ghi "`HOLOCRON_LLM_API_KEY` chưa đặt", đổi theo điều kiện đã cấu hình ở trên). Việc upload đề qua §6.1 đang dùng dữ liệu mẫu được báo riêng bằng `mock_extractor` (đề xuất, chờ duyệt; §17 D8), để UI không bỏ mất nhãn "dữ liệu mẫu" khi `demo_mode` đổi nghĩa.

### 11.3 Hợp đồng prompt viết template
System prompt gồm, theo thứ tự: (1) hợp đồng §8 nguyên văn; (2) danh sách import/builtin bị cấm của ASTGuard; (3) một ví dụ đầy đủ — chính file template RR; (4) slot cần sinh (`topic_key`, format, 3 `misconception_code` mục tiêu); (5) feedback lỗi của lượt trước theo định dạng §10. Output PHẢI là JSON khớp `QuestionTemplate` (không có trường `created_at`, `template_id`); JSON sai ⇒ `SCHEMA_INVALID` và trừ một lượt. Adapter không dựa vào `response_format` của nhà cung cấp để đảm bảo JSON (§11.5).

### 11.4 LLM là tuỳ chọn: chế độ khả năng (đã duyệt 2026-10-25)

Quy tắc "503 khi thiếu key" của v0.5 bị bỏ. Thay vào đó, `GET /v1/runtime` báo từng khả năng kèm `available` và lý do (§5.12). Chỉ endpoint thực sự cần một khả năng mới trả lỗi rõ ràng (409 `CapabilityError`, §6.0, mã đã duyệt 2026-10-25); mọi phần còn lại chạy offline. Cột "Khi không khả dụng" là đề xuất (chờ duyệt), trừ hành vi 409 `CapabilityError` không gọi mạng của khả năng cần LLM (dòng `concept_pair_llm_proposal`), đã duyệt 2026-10-27 (§11.2).

| Khả năng | Cần | Endpoint hoặc luồng bị ảnh hưởng | Khi không khả dụng |
|---|---|---|---|
| `ocr` | Tesseract + `vie.traineddata` đủ chữ. Kiểm đúng thư mục tessdata mà lần gọi OCR thật dùng: `--tessdata-dir` từ `HOLOCRON_TESSDATA_DIR` nếu đặt; không thì Tesseract 5.x dùng thư mục `--list-langs` in ra; Tesseract 4.x đường lùi `HOLOCRON_TESSDATA_DIR` → đường dẫn mặc định của bản cài. `TESSDATA_PREFIX` không được đọc và không được truyền cho Tesseract (đường lùi 4.x thực tế chỉ còn thư mục mặc định của bản cài và cạnh `tesseract.exe` trên Windows, vì `HOLOCRON_TESSDATA_DIR` khi đặt luôn được truyền). Thư mục đã xác định mà không tồn tại, thiếu `vie.traineddata` hoặc không phải file thường ⇒ `ocr_language_missing` (`detail_vi` nêu tên biến `HOLOCRON_TESSDATA_DIR`, không nêu đường dẫn); KHÔNG xác định được thư mục ⇒ bỏ qua kiểm unicharset, cảnh báo một lần (duyệt 2026-10-30; ADR-008 mục 14). Kiểm khởi động và worker gọi chung một hàm xác định thư mục. Kiểm chạy nền sau khi mở cổng; chưa xong ⇒ `local_check_pending` (§5.12) | Trang scan trong `POST /v1/documents` (§6.5) | Trang scan thành `failed` (`OCR_ENGINE_MISSING`). Trang `failed` không sửa và không xác nhận được trong MVP (duyệt 2026-10-26, §5.7, §6.5): người dùng cài Tesseract và `vie.traineddata`, rồi xoá tài liệu và nhập lại. Trang có lớp chữ vẫn đọc ở tầng 1 |
| `ocr_cross_check` | RapidOCR nạp được (kiểm trong tiến trình con có giới hạn lúc khởi động; không nạp được ⇒ `secondary_ocr_unavailable`), `HOLOCRON_PDF_OCR_SECONDARY` khác rỗng (để trống ⇒ `secondary_ocr_disabled`) (duyệt 2026-10-29); chưa kiểm xong ⇒ `local_check_pending` (duyệt 2026-10-30) | Đối chiếu chéo trang OCR (§7.4) | `cross_check = unavailable` ⇒ WARNING `CROSS_CHECK_UNAVAILABLE`, ô số không AUTO_PASS |
| `vault_search` | Không gì ngoài code (FTS5 hoặc BM25 tự cài) | `GET /v1/vault/search` (§6.6) | Luôn khả dụng khi đã có code |
| `theory_generation` | Không gì ngoài code | Slot lý thuyết `MCQ4` trong `POST …/twins` | Luôn khả dụng khi đã có code; thiếu cặp đã xác nhận thì `ReviewTicket` như ADR-007 |
| `concept_pair_llm_proposal` | LLM đã cấu hình | `POST /v1/vault/concept-pairs/propose` (§6.6) | 409 `CapabilityError`, không gọi mạng (duyệt 2026-10-27); người dùng tự tạo cặp bằng `POST /v1/vault/concept-pairs` |
| `essay_grading_offline` | Không gì ngoài code; phần tự luận có trong bản đang chạy | Slot `ESSAY` ở `POST /v1/blueprints`, `POST /v1/twins/{id}/essay-grade` (§6.9) | 409 `CapabilityError` với `reason = "not_implemented"` nếu phần tự luận bị chuyển sang sau MVP |
| `essay_grading_llm` | LLM đã cấu hình và có `AnswerJudge` (sau MVP) | Chưa có endpoint | Luôn `not_implemented` trong MVP |
| `template_authoring_llm` | LLM đã cấu hình | Slot tính toán trong `POST …/twins` (§10) | Không trả lỗi: pipeline dùng template đã kiểm chứng làm fallback như hiện nay (`UnconfiguredTemplateAuthor`); không có fallback thì `ReviewTicket` |
| `concept_auto_proposal` (đề xuất, chờ duyệt (2026-10-29)) | Chưa có code; khi làm thật không cần LLM | Chưa có endpoint | Luôn `not_implemented` trong bản hiện tại. UI dùng chức năng hiện có: người dùng tự tạo khái niệm bằng bôi chọn trong trang (hoặc chip chọn câu), kèm một câu giải thích ngắn; không hiển thị dữ liệu giả |
| `line_review_queue` (đề xuất, chờ duyệt (2026-10-29)) | Chưa có code; khi làm thật cần ADR I5 riêng (CLAUDE.md §9) | Chưa có endpoint | Luôn `not_implemented`. UI dùng duyệt theo trang hiện có (§6.5), kèm một câu giải thích ngắn; không hiển thị dữ liệu giả |
| `page_progress_eta` (đề xuất, chờ duyệt (2026-10-29)) | Chưa có code | Chưa có endpoint | Luôn `not_implemented`. UI chỉ hiện "x/y trang" từ trạng thái tài liệu có sẵn, kèm một câu giải thích ngắn; không hiện thời gian còn lại |
| `vlm` | Đủ ba biến `HOLOCRON_VLM_*` | Tầng 4, dual-pass VLM của §6.1 | Trang "cần xem kỹ" vẫn chờ người dùng xem lại; §6.1 dùng `MockDualPassExtractor` (`mock_extractor = true`; field đề xuất, chờ duyệt, §17 D8) |

- Đề xuất nhãn bằng LLM (`chapter_code`, `cognitive_level`, nhãn chunk) không có khả năng riêng trong danh sách đã duyệt: thiếu LLM thì giữ mặc định và người dùng sửa, không trả lỗi (§5.4, §6.7). Đề xuất (chờ duyệt): thêm khả năng `label_suggestion_llm` để UI báo rõ (§17 D10).
- Khả năng được tính một lần lúc khởi động (kiểm OCR chạy nền sau khi mở cổng, §5.12, duyệt 2026-10-30), từ cấu hình và kiểm tra cục bộ (binary, unicharset, FTS5); không gọi mạng để kiểm, nên LLM đã cấu hình mà gateway đang lỗi vẫn là `available = true` và lỗi lúc gọi trả 502 (§6.0).

### 11.5 Adapter LLM qua gateway (đã duyệt 2026-10-25)

Key hiện tại do ban tổ chức cấp, đi qua một gateway kiểu OpenAI, model `claude-haiku-5-5`. Nhà cung cấp do cấu hình quyết định, không có nhà cung cấp mặc định (duyệt 2026-10-25, §11.2). Adapter đặt ở `infrastructure/` sau `Protocol` `ChatCompletionClient` (tên đề xuất), cấp qua `Depends`; test thay bằng stub, không gọi mạng (CLAUDE.md §2.2). Contract hiện chỉ có một adapter, kiểu OpenAI Chat Completions; một gateway hay server local tương thích OpenAI dùng được qua adapter này chỉ bằng cấu hình. Anthropic gốc (API không theo giao thức OpenAI) cần một adapter riêng cài cùng `Protocol`, chưa có.

Adapter PHẢI:
- **Gửi header `User-Agent` riêng**, ví dụ `Holocron/<version>` (giá trị cụ thể là đề xuất). Gateway hiện tại chặn User-Agent mặc định của thư viện HTTP bằng 403 "error code: 1010".
- **Tự parse và kiểm JSON**, không tin `response_format`. Phản hồi được tách lấy khối JSON, parse bằng thư viện chuẩn, rồi validate bằng model Pydantic của đúng việc đang làm (ví dụ cặp khái niệm, §5.8; template, §11.3). Parse hoặc validate lỗi thì thử lại tối đa `LLM_JSON_RETRY_MAX` lần (đề xuất: 2; field của model cấu hình, không phải biến môi trường), hết lượt thì 502 (§6.0). Riêng việc viết template vẫn theo ngân sách §10 (`SCHEMA_INVALID` trừ một lượt).
- **Không đặt `temperature` hay `top_p` tuỳ ý.** Adapter không gửi hai tham số này, để model dùng giá trị mặc định của nhà cung cấp. Câu hỏi và đáp án không phụ thuộc tính tất định của LLM, vì mọi đầu ra đều qua cổng kiểm và người dùng xác nhận (§3.3). Dual-pass VLM ở §7.1 cũng không đặt nhiệt độ; hai lượt độc lập nhờ khác prompt (duyệt 2026-10-25).
- **Ghi số lần thử và tỉ lệ JSON lỗi** cho mỗi loại việc (§14). Không ghi nội dung prompt, phản hồi, key hay địa chỉ endpoint.

Ghi chú:
- **Dữ liệu gửi LLM đi qua gateway của ban tổ chức** (§13.1).
- **VLM bị park:** chưa kiểm gateway có nhận ảnh (`image_url`) hay không. Kiểm bằng script đánh giá opt-in (CLAUDE.md §2.2) trước khi bật lại tầng 4.
- **Model chạy local:** trỏ `HOLOCRON_LLM_BASE_URL` tới một server tương thích OpenAI trên máy người dùng. Tuỳ chọn; MVP chưa làm và chưa kiểm.

## 12. Knowledge Vault & trích dẫn 1-chạm

### 12.1 Bóc tách chunk
- Nguồn: chỉ văn bản người dùng đã xác nhận ở bộ nhập PDF (§6.5, ADR-006 mục 1). Code cắt chunk tất định; LLM không cắt chunk (ADR-006 mục 3).
- Cắt theo heading của slide/giáo trình; chunk dài hơn 450 token được tách tại ranh giới đoạn, chồng lấn 1 câu. Từ 2026-11-01 (ADR-010 mục 16; duyệt 2026-11-01 (ADR-010)) câu này chỉ áp cho đường `build_chunks`: tài liệu có `layout_version = null` (nhập trước khi có khối), tài liệu không phải PDF, và các dãy trang không có khối trong tài liệu có khối (trang OCR/bitmap tới khi X11 làm, trang `LAYOUT_FAILED`). Từ khoá tiêu đề phụ thuộc ngôn ngữ của `build_chunks` chuyển sang `heading_keywords` của `infrastructure/lang/{vi,en}.toml`, không đổi hành vi (ADR-010 mục 16).
- **Cắt theo khối (ADR-010 mục 16; duyệt 2026-11-01 (ADR-010)):** tài liệu có `layout_version` khác null, trang có khối: với mỗi Section (§5.15), lấy khối ỨNG VIÊN (§5.15; ở bước cắt chunk của settle, trùng tập đủ điều kiện sau khi `refresh_eligible_blocks` chạy) có `text` khác null theo (trang, `reading_order`), bỏ khối `heading` lặp do nối trang (giữ khối tiêu đề đầu), gom thành chunk ≤ 450 token (`search_tokens`) tại ranh giới khối; khối dài hơn được tách theo câu/từ bằng hàm hiện có. Không chồng lấn giữa các chunk (đề xuất suy ra). `raw_text` = `text` các khối nối bằng `\n\n`; chunk mang `section_id`, `block_ids` (§5.8); `page_number`/`page_end` = trang đầu/cuối của các khối. `figure`/`table` (`text = null`), `header_footer`, `page_number` không thành chunk. Khối ngắn hơn 8 ký tự ⇒ gộp với khối kề trong cùng Section; không có ⇒ không thành chunk, cờ `too_short_for_chunk`. `chunk_id = chunk_id_for(doc_id, ordinal, raw_text)` với một bộ đếm `ordinal` chung cho cả hai đường, theo thứ tự tất định ở §5.15 "Thứ tự chunk" (trang đầu, rồi `reading_order` của khối đầu; chunk `build_chunks` của dãy trang không có khối chen đúng vị trí trang). Trích dẫn mặc định như bước 4 hiện có (câu đầu ≥ 8 ký tự, ưu tiên khối không phải `heading`, ≤ 600 ký tự).
- Khi cắt, code đặt `category = THEORY` cho mọi chunk, `topic_keys = []` và `chapter_code` lấy từ tài liệu nhập (duyệt 2026-10-26, §6.5); code không tự phân loại nội dung. Tag `#theory` / `#practice_example` gắn tự động theo category (validator cưỡng chế).
- Tiêu chí gán `category` khi người dùng (hoặc LLM đề xuất, nếu có key) sửa nhãn qua `PATCH /v1/vault/chunks/{chunk_id}/labels` (§6.6): `THEORY` khi chunk chứa định nghĩa/định lý/công thức; `PRACTICE` khi chứa bảng số liệu hoặc lời giải ví dụ.
- `linked_chunk_ids`: chunk `PRACTICE` liên kết tới chunk `THEORY` có cùng `topic_keys` gần nhất về trang. Lúc cắt mọi chunk là `THEORY` với `topic_keys = []`, nên `linked_chunk_ids` khởi đầu rỗng. Tính lại khi nhãn đổi (duyệt 2026-10-26 chiều): sau mỗi `PATCH …/labels` thành công, code tính lại `linked_chunk_ids` cho mọi chunk cùng `subject_code` với chunk vừa sửa (không chỉ cùng `doc_id`), trong cùng giao dịch ghi. Việc tính lại KHÔNG tăng `version` của các chunk khác (chỉ chunk vừa sửa tăng `version`), để sửa nhãn một chunk không làm `expected_version` của chunk khác lệch. Với mỗi `topic_key` của chunk `PRACTICE`, chunk `THEORY` "gần nhất" xét lần lượt: cùng tài liệu trước tài liệu khác, khoảng cách trang nhỏ hơn, trang nhỏ hơn, `chunk_id` nhỏ hơn; danh sách sắp theo `chunk_id`, không trùng (`link_practice_chunks`, `domain/vault.py`). Chunk `THEORY` và chunk có `topic_keys = []` không liên kết.

### 12.2 Trích dẫn 1-chạm
- **MVP (duyệt 2026-10-26 chiều):** chưa render PDF; bấm trích dẫn hiện `verbatim_quote` dạng trích dẫn kèm `doc_id` và `page_number`. Hai gạch đầu dòng ngay dưới đây (pdfjs) là việc sau MVP (§1.6); gạch đầu dòng cuối (tài liệu gốc đã bị xoá) không đổi.
- Client gọi PDF.js `PDFFindController` với `query = verbatim_quote` trên đúng `page_number`; highlight trong Split-View.
- Không tìm thấy (ligature, gạch nối cuối dòng) ⇒ tìm theo 8 từ đầu của quote; vẫn không thấy ⇒ mở đúng trang và hiện quote dạng trích dẫn.
- Tài liệu gốc đã bị xoá ⇒ hiện quote dạng trích dẫn kèm thông báo "tài liệu gốc đã bị xoá" (ADR-006 mục 7).

### 12.3 Tìm kiếm và nguồn trích dẫn (ADR-006 mục 4–6)
- Tìm bằng BM25 trên token âm tiết, lọc theo `subject_code` (và `topic_keys` nếu có); SQLite FTS5 khi có, BM25 tự cài khi không (§5.13). Endpoint ở §6.6. Không dùng embedding trong MVP.
- **Tra cứu khối (ADR-010 mục 18; duyệt 2026-11-01 (ADR-010)):** endpoint MỚI `GET /v1/vault/blocks/search` (§6.10) trên tập khối đủ điều kiện (§5.15): bảng FTS khối có điều kiện + đường lùi Python (§5.13), hàm gấp dấu dùng chung lúc lập chỉ mục và truy vấn, token kỹ thuật khớp chính xác không phân biệt hoa thường xếp trên kết quả mờ, chỉ mục cả ô bảng, nhãn figure (kèm cờ), chú thích. Trả khoảng khớp trong chữ gốc để frontend tô. Tìm kiếm chunk ở trên không đổi.
- `VaultCitationProvider` thay `NoCitationProvider`: slot tính toán lấy chunk `THEORY` cùng `subject_code` có `topic_keys` chứa `topic_key` của slot; hoà điểm thì chọn trang nhỏ nhất rồi `chunk_id` nhỏ nhất; không có thì `ReviewTicket` như hiện nay. Slot lý thuyết lấy trích dẫn từ cặp khái niệm đúng. Chữ ký `citation_for` thêm `subject_code` (đã duyệt 2026-10-25, §5.8).

## 13. Bảo mật & Threat Model

| STRIDE | Mối đe doạ | Biện pháp |
|---|---|---|
| Spoofing | File giả định dạng (đổi đuôi, sai `Content-Type`) | Nhận diện bằng magic bytes; khai báo lệch ⇒ 415 |
| Tampering | Prompt injection trong ảnh/PDF khiến LLM sinh template độc hại | ASTGuard + sandbox L1–L3; template không bao giờ chạy ngoài sandbox |
| Tampering | PDF độc hại khai thác parser hoặc OCR | Đọc trong worker riêng có giới hạn OS, timeout theo trang, không chạy JavaScript/XFA, Tesseract chỉ nhận ảnh PNG đã render (ADR-008 mục 3, 4, 8) |
| Tampering | VLM bị lừa bởi chữ ẩn trong ảnh, đọc sai số | Dual-pass + self-solve mismatch + con người xác nhận (I5) |
| Repudiation | Không biết ai sửa ô nào | `PatchAudit` cho mọi PATCH |
| Information disclosure | Lộ API key BYOK | Key chỉ ở biến môi trường backend; không log; không trả về client; Frontend không bao giờ giữ key |
| Information disclosure | Nội dung tài liệu học rời máy khi dùng LLM | LLM là tuỳ chọn (§11.4); khi dùng, dữ liệu đi qua gateway của ban tổ chức và UI nói rõ điều này (§13.1) |
| Spoofing | Trang web khác gọi API local (CSRF, DNS rebinding) | Kiểm `Host` luôn bật; kiểm `Origin` cho request ghi (hướng duyệt 2026-10-26 chiều); token khi đặt `HOLOCRON_REQUIRE_TOKEN` (§13.1) |
| Denial of service | File lớn; PDF nhiều trang; template vòng lặp vô hạn/memory bomb | Upload đề (§6.1): 10 MiB. Nhập tài liệu (§6.5): 50 MiB, 300 trang, 25 megapixel mỗi trang, timeout 5 s/30 s mỗi trang, RAM worker 2 GiB (ADR-008). Sandbox: wall-clock 2 s; RLIMIT/Job Object |
| Elevation of privilege | Escape sandbox qua introspection/module graph | Cấm thuộc tính `_*` và các tiền tố frame; proxy module không lộ module con; test bảo mật bắt buộc khi nới whitelist |

Backend PHẢI chỉ lắng nghe `127.0.0.1` trong bản local-first.

### 13.1 Mô hình bảo mật của app local
- **Không có đăng nhập.** App dành cho một người dùng trên một máy; ranh giới tin cậy là tài khoản hệ điều hành của người dùng. Mọi tiến trình chạy dưới tài khoản đó đều đọc được dữ liệu của app, và đây là rủi ro được chấp nhận.
- **API chỉ nghe `127.0.0.1`** (đã triển khai). App PHẢI chặn thêm hai đường tấn công từ trình duyệt: trang web độc hại gọi `http://127.0.0.1:<port>` (CSRF) và DNS rebinding. Biện pháp (đã duyệt 2026-10-25):
  - **Kiểm `Host` luôn bật**, ở mọi chế độ chạy (webapp localhost và Tauri): backend chỉ nhận `Host` là `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>`, hoặc ba tên máy đó không kèm cổng (`127.0.0.1`, `localhost`, `[::1]`), ngược lại trả 403 (§6.0) (thêm `[::1]` và dạng không kèm cổng cho khớp code, duyệt 2026-10-26 chiều). Kiểm bằng middleware ASGI `LocalAccessMiddleware` chạy trước routing và trước khi đọc body, nên áp cho mọi request: route API, `/openapi.json`, `/docs`, `/redoc`, path không tồn tại (404), method sai (405) và file tĩnh của chế độ portable. Danh sách host lấy từ provider `get_allowed_hosts`; middleware tra `app.dependency_overrides` trước khi gọi provider, nên test override như một dependency FastAPI bằng một fixture `autouse` trong `tests/conftest.py`, không sửa test sẵn có. Có `X-Forwarded-Host` (proxy rewrite của Next) thì mọi tên host trong đó cũng phải thuộc danh sách. `TestClient` của Starlette mặc định gửi `Host: testserver`, nhưng `testserver` KHÔNG được đưa vào danh sách cho phép mặc định (duyệt 2026-10-25, §17 D13).
  - **Token chỉ bắt buộc khi đặt `HOLOCRON_REQUIRE_TOKEN`** (chế độ chạy dưới Tauri, §11.2). Khi đó Tauri sinh token ngẫu nhiên mỗi lần khởi động, truyền cho sidecar qua biến này và cho WebView của app; mọi request PHẢI mang token trong header `X-Holocron-Token` (tên header đã duyệt 2026-10-25), so bằng phép so thời gian hằng (`hmac.compare_digest`); thiếu hoặc sai ⇒ 403. Token lấy từ provider `get_required_token` (cũng tra override như trên). **Duyệt 2026-10-29 (câu 6), thay quyết định câu 21 (a) ngày 2026-10-28:** biến không đặt hoặc `""` ⇒ không yêu cầu token; có giá trị mà toàn khoảng trắng (mọi khoảng trắng Unicode) hoặc không hợp lệ theo `is_valid_token` (ký tự ngoài ASCII in được kể cả NBSP, U+3000, `\n`, `\r`, ký tự điều khiển, hoặc khoảng trắng đầu/cuối) ⇒ cấu hình hỏng, app từ chối khởi động, thông điệp chỉ nêu tên biến, không nêu giá trị. Hành vi theo từng đường chạy (đã kiểm ở code `holocron_core/launcher.py` và `routers/api.py`): (a) bản zip (launcher): kiểm TRƯỚC khi mở cổng, in URL hay mở trình duyệt; lỗi chỉ nêu tên biến, in ra stderr và file log, thoát mã 2; nếu server không khởi động xong thì cũng thoát 2, và lỗi "server chưa khởi động xong" cũng được in ra stderr và file log (như lỗi token); lỗi `lifespan` khác thì mã 3 với uvicorn ghim trong bản zip (`uvicorn==0.54.0`, `holocron/packaging/requirements-portable.txt`); bản uvicorn cũ hơn trả về mà server chưa khởi động ⇒ launcher thoát 2. (b) `uvicorn holocron_core.routers.api:app` (không `--reload`): lifespan từ chối, uvicorn báo "Application startup failed", thoát mã 3. (c) `--reload`: tiến trình worker thoát mã 3, tiến trình reloader vẫn chạy (chỉ đọc mã, chưa đo). Lúc chạy middleware vẫn 403 fail-closed với `detail` cố định như lớp thứ hai. Không đặt biến thì không yêu cầu token, như giai đoạn webapp hiện nay.
  - **Kiểm `Origin` cho request ghi (CSRF).** Hướng đã duyệt 2026-10-26 chiều; toàn bộ chi tiết dưới đây là đề xuất (chi tiết), chờ duyệt. Kiểm `Host` chặn DNS rebinding nhưng không chặn một trang web khác gửi form hoặc `fetch` tới `http://127.0.0.1:<port>` (Host khi đó vẫn hợp lệ); kiểm `Origin` lấp chỗ này khi chưa bật token.
    - **Method:** chỉ kiểm request ghi, tức `POST`, `PUT`, `PATCH`, `DELETE`. `GET`, `HEAD`, `OPTIONS` không kiểm (không đổi dữ liệu; đọc chéo origin đã bị trình duyệt chặn vì backend không bật CORS, §6.0).
    - **`Origin` có mặt:** so khớp chính xác (sau khi đưa scheme và tên máy về chữ thường) với danh sách origin cho phép; không khớp, kể cả giá trị `null` (iframe sandbox, `file://`), ⇒ 403.
    - **`Origin` vắng mặt:** cho qua, vì client không phải trình duyệt (curl, script đánh giá, `TestClient`) không gửi header này, còn trình duyệt hiện đại luôn gửi `Origin` với request `POST`/`PUT`/`PATCH`/`DELETE`. Phòng thêm cho trình duyệt cũ: `Origin` vắng mặt mà có `Sec-Fetch-Site: cross-site` ⇒ 403. Tiến trình cục bộ không phải trình duyệt nằm trong ranh giới tin cậy của tài khoản hệ điều hành (gạch đầu dòng đầu của mục này), nên cho qua không mở thêm đường tấn công.
    - **Danh sách origin cho phép:** `http://127.0.0.1:<port>`, `http://localhost:<port>`, `http://[::1]:<port>`, với `<port>` là cổng bất kỳ, kể cả không ghi cổng. Lý do nhận mọi cổng: ở chế độ webapp, trang ở cổng của Next dev (mặc định 3000) còn backend ở 8000, và rewrite của Next chuyển tiếp nguyên `Origin`; mọi tiến trình phục vụ trên loopback thuộc cùng ranh giới tin cậy ở trên, và chúng vốn gọi được API mà không cần trình duyệt. Chỉ nhận scheme `http` (backend local không có TLS). Bản Tauri phương án B (`docs/tauri-spike-plan.md`): trang do chính backend phục vụ nên origin là `http://127.0.0.1:<port>` của backend, đã nằm trong danh sách; không thêm origin riêng của Tauri. Nếu phải dùng phương án A (dự phòng), origin của WebView Tauri v2 (`tauri://localhost` trên Linux, `http://tauri.localhost` trên Windows; giá trị thật ghi lại ở bước 4 của spike) được thêm vào danh sách cùng lúc với việc sửa quy ước CORS ở §6.0.
    - **Cấp danh sách:** qua provider tra override trong middleware, như danh sách host (D13); test override bằng cùng fixture `autouse` trong `tests/conftest.py`, không sửa test sẵn có.
    - **Thứ tự kiểm và phản hồi:** `Host` → `Origin` → token. Mỗi bước trả 403 với `detail` là chuỗi cố định riêng; với `Origin`: "Yêu cầu ghi từ nguồn không được phép". `detail` không lặp lại giá trị `Origin`, `Host` hay token đã gửi.
    - **Test:** thêm vào `tests/test_local_security.py` (§15): `POST` có `Origin: https://evil.example` ⇒ 403; `Origin: null` ⇒ 403; `Origin: http://localhost:3000` ⇒ qua; không có `Origin` ⇒ qua; không có `Origin` mà có `Sec-Fetch-Site: cross-site` ⇒ 403; `GET` có `Origin` lạ ⇒ không bị chặn bởi bước này.
  - Trạng thái: kiểm `Host` và token đã triển khai (PR #81; `routers/access.py`, đăng ký ở `routers/api.py`); xử lý token rỗng/sai dạng theo câu 6 ngày 2026-10-29 (thay câu 21 (a)) đã triển khai ở PR sửa token (PR #97); kiểm `Origin` chưa triển khai. Lịch chốt 2026-10-26: kiểm `Host` và token (phần đã làm ở nhánh B4) vào MVP-1; kiểm `Origin` làm sau MVP-1 (§1.5).
  - **API key (BYOK).** Giai đoạn webapp: biến môi trường (danh sách ở §11.2, mẫu ở `.env.example` chỉ chứa placeholder). Backend không tự đọc file `.env` (không có dependency nạp `.env`); nếu người dùng giữ key trong file `.env` cục bộ thì file đó đã nằm trong `.gitignore` và phải được nạp vào shell trước khi chạy backend. Bản desktop: lưu trong kho khoá của hệ điều hành (Windows Credential Manager; Secret Service trên Linux); Tauri đọc ra và truyền cho sidecar qua biến môi trường lúc khởi động. Key KHÔNG ĐƯỢC ghi ra file cấu hình dạng rõ, KHÔNG ĐƯỢC ghi log, KHÔNG ĐƯỢC trả về client (`GET /v1/runtime` chỉ trả các field ở §5.12: boolean, mã lý do của từng khả năng, `storage_mode`, `llm_data_recipient`, `app_version`, `commit`, `log_dir`; không có key, tên model hay endpoint; cập nhật duyệt 2026-10-28).
- **Dữ liệu rời máy.** PDF, Vault và cơ sở dữ liệu nằm trong thư mục dữ liệu ứng dụng của người dùng. Không cấu hình LLM và VLM thì không dữ liệu nào rời máy (G6). Dữ liệu chỉ rời máy ở các chỗ sau (hai chỗ ban đầu; từ 2026-10-31 thêm tên chương, cùng endpoint LLM):
  - Văn bản đã tách (văn bản chunk, văn bản trang đề) gửi tới endpoint LLM đã cấu hình để đề xuất nhãn, đề xuất cặp khái niệm và viết template. **Với key hiện tại, dữ liệu này đi qua gateway của ban tổ chức hackathon** (đã duyệt 2026-10-25, §11.5), tức bên vận hành gateway và nhà cung cấp model phía sau có thể thấy nội dung. Câu trả lời tự luận của người học không rời máy khi chưa có `AnswerJudge`.
  - Thêm 2026-10-31 (GĐ1 1A): khi có LLM, bước chọn tên chương gửi danh sách ứng viên gồm tên file (đã làm sạch) và các dòng header lặp của tài liệu tới cùng endpoint LLM đó (§5.7 "Chương tự động"); không gửi trang bìa hay văn bản trang. Job nền đề xuất cặp gửi văn bản chunk như `propose` (§5.8).
  - Ảnh trang gửi tới VLM khi người dùng đã cấu hình VLM (đang park).
  - Thêm 2026-11-01 (ADR-010 mục 9, 19; duyệt 2026-11-01 (ADR-010)): ảnh cắt vùng (`CropImage`) chỉ rời máy khi gửi tới VLM theo công tắc đồng ý gửi ảnh (file quyết định mục 3) và luật chốt ở X02b (ADR-009 bản cập nhật, merge SAU ADR-010; tới khi đó đường VLM không bật); bộ dựng khối, lưu và phục vụ ảnh cắt không gọi mạng.

  UI PHẢI nói rõ hai điểm này ở màn hình cấu hình key, kèm tên bên nhận dữ liệu theo cấu hình đang dùng (ví dụ "gateway của ban tổ chức"). Nguồn của tên này: `RuntimeInfo.llm_data_recipient`, nhãn người dùng đặt qua `HOLOCRON_LLM_DATA_RECIPIENT` (hướng duyệt 2026-10-26 chiều; tên field, tên biến và quy tắc là đề xuất (chi tiết), chờ duyệt, §5.12, §11.2).
- **Ảnh cắt (ADR-010 mục 19; duyệt 2026-11-01 (ADR-010)).** Ảnh do worker render từ file không tin cậy, qua `CropRequest` có trần (§5.7); tiến trình cha kiểm magic PNG, IHDR, cạnh, byte, sha256 trước khi lưu. Lưu ở `HOLOCRON_DATA_DIR/crops/<document_id>/<crop_ref>.png`, đường dẫn chỉ dựng từ id đã kiểm mẫu (không thoát thư mục dữ liệu; có test). Endpoint `GET /v1/documents/{document_id}/crops/{crop_ref}` (§6.10) trả `Content-Type: image/png`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=31536000, immutable`, chỉ phục vụ ảnh của đúng tài liệu; Host/token kiểm như mọi request. Frontend tải bằng `lib/api.ts` → blob → object URL; CẤM token trong URL.

## 14. Quan sát hệ thống
- Log có cấu trúc cho mỗi lần sandbox: `template_sha256, seed, status, duration_ms, stage`.
- Log cho mỗi upload: `document_id, media_type, size_bytes, gate_status, số issue theo mã` (không log nội dung file).
- Log cho mỗi trang của bộ nhập (§6.5): `document_id, page_number, tier_used, status, review_reasons, failure_reason, duration_ms` (không log văn bản trang).
- Log cho mỗi lần gọi LLM qua adapter (§11.5): loại việc, số lần thử, kết quả (`ok`, `json_invalid`, `schema_invalid`, `http_error`, `timeout`), mã HTTP, thời gian. KHÔNG log prompt, phản hồi, key, tên model hay địa chỉ endpoint.
- Log cho mỗi lần chấm tự luận (§6.9): `twin_id`, `grader_version`, `total_points`, `matched_count`, độ dài câu trả lời; không log văn bản câu trả lời.
- Metrics: tỉ lệ chấp nhận seed theo template; phân bố `assertion_histogram`; tỉ lệ `AUTO_FALLBACK`; tỉ lệ Gate `BLOCKED` theo mã issue; số lần 409 version; số lần thử trung bình và tỉ lệ JSON lỗi của LLM theo loại việc (đã duyệt 2026-10-25).

**Log file của bản portable (2026-10-28; đã triển khai, PR #80).** Launcher ghi log ra file `%LOCALAPPDATA%\Holocron\logs\holocron.log` (Windows) hoặc `$XDG_STATE_HOME/holocron/logs` (nơi khác); luôn ở vị trí này kể cả khi có `--data-dir`. Xoay vòng: file hiện tại cộng tối đa 5 bản xoay (`holocron.log.1`–`.5`), mỗi file tối đa 5 MB (tổng ~30 MB). Đường lui: Windows thiếu `LOCALAPPDATA` thì dùng `~/AppData/Local`; nơi khác thiếu `XDG_STATE_HOME` thì dùng `~/.local/state`. Ghi logger `holocron_core` từ mức INFO; từ uvicorn chỉ có những dòng mức WARNING trở lên; console vẫn in từ WARNING. Giá trị biến môi trường có tên chứa KEY, TOKEN, SECRET hoặc PASSWORD (từ 8 ký tự) bị che bằng `***` trên message, traceback và stack. `Mo-thu-muc-log.cmd` mở thư mục log. `app/holocron_build.json` (`version`, `commit`, `dirty`, `built_at`) ghi thông tin bản build; launcher in `Holocron <version> (<commit7>)`. `GET /v1/runtime` trả `app_version`, `commit` (từ file build này) và `log_dir` (thư mục log ở trên; đọc từ biến `HOLOCRON_LOG_DIR` do launcher quản lý: launcher đặt biến khi mở được file log, xoá khi không mở được, người dùng không đặt tay, §11.2; `null` khi biến không được đặt) (§5.12, duyệt 2026-10-28). Giới hạn đã biết: traceback có thể chứa văn bản tài liệu (lệch quy tắc "không log văn bản trang" ở trên; ghi là nợ PKG-003, ADR-004); hai phiên bản chạy với `--data-dir` khác nhau ghi chung một file; trên Windows, hai tiến trình cùng mở log thì lúc xoay vòng (rename) có thể lỗi `PermissionError`, `logging` in lỗi ra stderr và mất dòng log.

## 15. Kiểm thử & Định nghĩa "Done"

| Loại | Nội dung | File |
|---|---|---|
| Security | Payload escape bị ASTGuard chặn; proxy module; timeout; memory bomb | `tests/test_sandbox_security.py` |
| Correctness | `solve()` so với bộ giải tham chiếu độc lập trên ≥ 100 seed; tính tất định | `tests/test_rr_template.py` |
| Gate E2E | Đọc sai ô → BLOCKED → sửa → AUTO_PASS → approve; 409 version | `tests/test_schemas_and_gate.py` |
| Upload | Dual-pass mock → BLOCKED đúng ô; 400/413/415/422; `document_id` tất định | `tests/test_upload.py` |
| Trạng thái item | 422 quantum null; 400 approve/non-cloneable | `tests/test_api_item_state.py` |
| Pipeline | Misconception lạ ⇒ retry, không crash | `tests/test_pipeline_twin_assembly.py` |
| I3 (NFC) | Trích dẫn ở dạng NFD vẫn khớp `raw_text` dạng NFC; trích dẫn không có trong văn bản vẫn bị loại | `test_chunk_accepts_quote_in_nfd_against_nfc_text` và `test_chunk_rejects_quote_not_in_text` (`tests/test_schemas_and_gate.py`) |
| Bộ nhập PDF (ADR-008 mục 14) | Unicharset đủ 134 chữ tiếng Việt (trên file mẫu), cho phép thiếu các chữ trong `OCR_CHARSET_ALLOWED_MISSING` (mặc định `Ỵ`, `ỵ`) kèm cảnh báo (duyệt 2026-10-25); độ phủ dòng thấp ⇒ `needs_review`; token lệch ⇒ `DUAL_PASS_MISMATCH`; `cross_check = unavailable` ⇒ không AUTO_PASS | `tests/test_pdf_ocr.py` (`test_ocr_charset_covers_vietnamese`, `test_low_line_coverage_marks_page_needs_review`, `test_numeric_token_mismatch_raises_dual_pass_mismatch`, `test_cross_check_unavailable_blocks_auto_pass`) |
| Bộ nhập PDF | Cờ trang chọn đúng tầng cho PDF số và trang scan | `tests/test_pdf_tiers.py` (`test_page_flags_select_tier`) |
| Bộ nhập PDF | Magic bytes, số trang, timeout trên file mẫu tự tạo | `tests/test_pdf_input_limits.py` (`test_bad_pdf_limits`) |
| Lọc trích dẫn theo môn (§5.8, đã duyệt 2026-10-25) | Chunk của môn khác không bao giờ được chọn, kể cả khi `topic_keys` khớp | test mới trong `tests/test_twin_batch.py` hoặc file test của `VaultCitationProvider` (tên đề xuất) |
| Chấm tự luận offline (§5.14; tên file đã có ở dòng I1, I3 và I4 của bảng CLAUDE.md §9, dòng I1 và I3 duyệt 2026-10-26 chiều, §17 D11; file chưa có) | Cùng đầu vào cho cùng `EssayGradeResult` từng byte; câu trả lời NFC và NFD cho cùng kết quả; mọi ý là chuỗi con của `definition_quote`; tách ý gộp đoạn ngắn đúng; câu trả lời chép nguyên văn khớp đủ y/y ý; câu trả lời rỗng ý khớp 0 ý; `matched_count + len(missing_points) = total_points` | `tests/test_essay_grader.py` |
| Chế độ khả năng (§11.4; tên file đề xuất) | Thiếu từng biến LLM ⇒ đúng khả năng bị báo `llm_not_configured`; endpoint cần LLM trả 409 `CapabilityError`; endpoint offline vẫn chạy; `GET /v1/runtime` trả đủ 12 khả năng đúng thứ tự §5.12, ba tên đề xuất, chờ duyệt (2026-10-29) luôn `not_implemented` kể cả khi LLM và VLM đủ cấu hình; `GET /v1/runtime` chỉ gồm các field ở §5.12 (boolean, mã lý do, `storage_mode`, `llm_data_recipient`, `app_version`, `commit`, `log_dir`; duyệt 2026-10-28) và không lộ key, tên model hay endpoint | `tests/test_runtime_capabilities.py` |
| Adapter LLM (§11.5; tên file đề xuất) | Có `User-Agent` riêng; không gửi `temperature`/`top_p`; JSON lỗi ⇒ thử lại đúng số lần rồi 502; đếm số lần thử; dùng stub HTTP, không gọi mạng | `tests/test_llm_adapter.py` |
| Bảo mật app local (§13.1; tên file đề xuất) | `Host` lạ ⇒ 403; `Host` `[::1]:<port>` và dạng không kèm cổng ⇒ qua (duyệt 2026-10-26 chiều); request ghi có `Origin` lạ hoặc `null` ⇒ 403, không có `Origin` ⇒ qua (đề xuất (chi tiết), chờ duyệt, §13.1); có `HOLOCRON_REQUIRE_TOKEN` thì thiếu hoặc sai token ⇒ 403, đúng token ⇒ qua; không đặt biến hoặc `""` thì không cần token; giá trị toàn khoảng trắng hoặc sai dạng ⇒ app từ chối khởi động (mã thoát tuỳ đường chạy, §13.1; thông điệp chỉ nêu tên biến) và middleware 403 fail-closed như lớp thứ hai (duyệt 2026-10-29, câu 6) | `tests/test_access_guard.py` (M10), `tests/test_launcher_token_config.py` |
| Frontend | `tsc --noEmit` và `next build` sạch | `holocron/frontend` |

"Done" cho một thay đổi: `pytest`, `ruff check .`, `ruff format --check .`, `mypy --strict holocron_core` xanh; frontend `npm run typecheck` và `npm run build` xanh khi chạm `frontend/`; walkthrough sinh lại `examples/*.json` khi đổi đầu ra pipeline.

"Done" cho một topic mới (đổi theo ADR-005, 2026-10-24): cặp `.py` + `.json` trong `infrastructure/templates/`, trong đó `.json` khai báo `input_schema` (§5.10) và `required_artifacts`; bộ giải tham chiếu độc lập; test tái lập I4 và property test ≥ 100 seed tương đương `tests/test_rr_template.py` (CLAUDE.md §9); tỉ lệ chấp nhận seed ≥ 10 %. Không thêm hằng hay bảng riêng cho topic trong `domain/`, `services/`, `routers/` hay `frontend/` (thay yêu cầu cũ về `TABLE_SCHEMAS`/`TOPIC_ALGORITHMS`).

## 16. Lộ trình

| Mốc | Hạng mục | Trạng thái |
|---|---|---|
| T2-N1 | Schemas, sandbox, template RR, Gate API | Xong |
| T2-N2 | Spec v0.3; upload + mock dual-pass; `CPUSchedulingProblem`; Frontend Ingestion (upload, Table Auditor) | Xong |
| T2-N3 | `solve()` của template RR trả `gantt` đúng dạng §8.1; `GanttSegment` và `TwinQuestion.gantt` (§5.6) | Xong |
| T2-N3 | Template SJF không ưu tiên (`os_sjf_avg_waiting.py`, `031e26a`) | Xong: đăng ký trong kho template đã kiểm chứng, dùng được khi sinh twin; chưa có trong `TABLE_SCHEMAS`/`TOPIC_ALGORITHMS` và extractor mock nên chưa nhập đề SJF qua upload được |
| T2-N3 | `infrastructure/llm_config.py` (`7e2371e`); `GET /v1/runtime` với cờ `demo_mode` (§6.4) | Xong; adapter LLM/VLM thật chưa có nên luôn ở chế độ demo, và Frontend chưa đọc `demo_mode` |
| T2-N3 | `POST /v1/blueprints` | Kế hoạch (chưa đủ chi tiết về nguồn dữ liệu của slot, xem §6.3) |
| T2-N4 | `POST /v1/blueprints/{id}/twins` | Xong, với nguồn trích dẫn và author mặc định chưa cấu hình (§6.3); twin trong batch còn trùng seed (R6) |
| T2-N4 | Twin Viewer + Gantt chart trên Frontend | Xong: trang `/blueprints/[blueprintId]` gọi `POST …/twins` và hiển thị 3 twin, badge misconception, trích dẫn, `review_tickets`; chưa nối từ luồng ingestion vì `POST /v1/blueprints` chưa có (§6.3) |
| T2-N4 | Trả nợ FE-001: cảnh báo Biome ở `TableAuditor.tsx` (ADR-004) | Xong (`5f3b2b4`, `1b9b5c4`) |
| T2-N5 | Adapter VLM thật cho dual-pass; pool tiến trình sandbox | VLM park (2026-10-25, R4); pool tiến trình: kế hoạch |
| T2-N6/N7 (24–25/10) | Spec v0.4 §1, §0 (I2, I4) và §13.1; ADR-005…008 (*Chấp nhận*); engine OCR ADR-008 (chốt 2026-10-24); spec v0.5 contract cho bộ nhập PDF, Vault, blueprint, SQLite (Đợt 2b) | Xong phần tài liệu |
| 25/10 | Spec v0.6: quyết định ngày 2026-10-24/25 (§1.2, §1.5, §5.12, §5.14, §6.8, §6.9, §11.4, §11.5, §13.1, §16); kế hoạch spike Tauri (`docs/tauri-spike-plan.md`) | Xong phần tài liệu |
| 25/10 (tối) | Spec v0.7: quyết định duyệt tối 2026-10-25 (§0 I1/I3/I4, §5.4, §5.7, §5.11–§5.14, §6.0, §6.4, §7.1, §7.4, §10.1 nháp D6, §11.2, §13.1, §17); sửa câu chữ ADR-004…008 | Xong phần tài liệu |
| 26/10 | Spec v0.7, bổ sung: quyết định duyệt 00:25 ngày 2026-10-26 (§5.7–§5.9, §5.11, §5.13, §6.5, §12.1, §17 D8); ADR-007 mục 2 (dạng câu do RNG chọn) | Xong phần tài liệu |
| 26/10 (chiều) | Spec v0.7, bổ sung chiều: quyết định duyệt chiều 2026-10-26 (§0 cơ chế I1, §1.6, §4, §5.6–§5.9, §5.11–§5.13, §6.0, §6.3, §6.5, §6.6, §11.2, §12.1, §12.2, §13, §13.1, §15, §17 D8, D9, D11, D14); ADR-004 (sổ nợ), ADR-006 mục 2, ADR-007 mục 1–2; `docs/tauri-spike-plan.md` chọn phương án B | Xong phần tài liệu |
| 26/10 (chiều, đợt hai) | Spec v0.7, bổ sung: phạm vi MVP-1 và lịch mới (§1.4–§1.6, §16); quyết định câu 19 (§0 câu chữ I3/I4 theo D11(b)(c), §5.12, §5.14, §10.1, §11.2, §17 D6, D7, D11); sửa theo review (§4, §5.7–§5.9, §5.11, §5.12, §6.6, §17 D8, D9); ADR-007 mục 1, 2, 5a | Xong phần tài liệu |

**Lịch từ 2026-10-26 (chốt 2026-10-26; thay lịch 2026-10-25 ở dưới). Hạn nộp hackathon: Chủ nhật 8/11/2026.**

| Mốc | Hạng mục | Trạng thái |
|---|---|---|
| **26–29/10: MVP-1** (mục tiêu duy nhất tới hết 29/10) | Phạm vi ở §1.5: bản Windows zip portable, offline, không cần key; tạo môn → nhập PDF (lớp chữ + OCR) → xem lại trang → xác nhận vào Vault → xác nhận cặp khái niệm → câu MCQ4 lý thuyết tất định → làm bài → đáp án kèm trích dẫn nguyên văn và số trang → lịch sử làm bài và ôn lại câu sai (contract lịch sử: đề xuất riêng, chờ duyệt). Blueprint: chỉ làm luồng slot lý thuyết, cùng các endpoint GET blueprint (§6.7); giữ `POST /v1/blueprints` cho slot lý thuyết là suy ra, chờ người dùng xác nhận (§1.5, §17 D8); không đổi contract §6.7 và không đổi kiểm I5 (item `approved=true`) cho `item_ids`. Đóng gói bằng Python embeddable, backend phục vụ frontend tĩnh và tự mở trình duyệt; launcher đặt `HOLOCRON_DATA_DIR` để lưu SQLite (§1.5); người dùng tự cài Tesseract; không PyInstaller | Kế hoạch |
| **30/10–2/11** | Tự luận chấm offline (§5.14, §6.9); giải thích đáp án bằng LLM, TUỲ CHỌN: chỉ khi người dùng tự thêm key, neo trích dẫn nguyên văn (I3), lưu lại và không gọi lại cho cùng câu, thiết kế chờ duyệt (§1.6); câu tính toán, phần khả thi nhất | Kế hoạch |
| **3–6/11** | Đóng gói Tauri; thử chữ viết tay ở mức thử nghiệm; sửa lỗi | Kế hoạch |
| **7–8/11: đóng băng code** | Chỉ sửa lỗi và tài liệu nộp | Mốc |

- Sau MVP-1 còn: câu tính toán, tầng 3 (bảng) (suy ra từ phạm vi MVP-1 chỉ gồm lớp chữ + OCR), `capabilities`, kiểm `Origin`, `RuntimeInfo` mở rộng (§1.5). Lịch trên chỉ xếp tự luận, giải thích bằng LLM, phần khả thi nhất của câu tính toán và Tauri; các phần còn lại chưa có mốc.

**Lịch từ 2026-10-25 (đã duyệt 2026-10-25; thay thứ tự code ghi ngày 2026-10-24; bổ sung tối 2026-10-25). Đã bị lịch 2026-10-26 ở trên thay; giữ làm lịch sử:**

| Mốc | Hạng mục | Trạng thái |
|---|---|---|
| **Lát cắt dọc, ưu tiên số 1, mục tiêu 28/10** | PDF → Vault → câu lý thuyết MCQ → màn luyện tập (§4). Gồm: model dùng chung ở `domain/` (Đợt 2c); bộ nhập PDF tầng 1–2 và màn hình xem lại (§6.5); Vault, cặp khái niệm do người dùng bôi (LLM đề xuất nếu có), `VaultCitationProvider` với `citation_for(subject_code, slot)` (§5.8, §6.6); bộ sinh câu lý thuyết và `POST /v1/blueprints` với `theory_slots` (§6.7); màn luyện tập MCQ. Lát cắt không cần LLM, sandbox hay template | Kế hoạch |
| **Thứ Tư 28/10: MVP cơ bản** | Lát cắt dọc chạy được end-to-end trên ít nhất một môn, trên máy người dùng | Mốc |
| **Hết 29/10: mốc quyết định tự luận** | Tự luận là "MVP nếu còn thời gian" (duyệt 2026-10-25). Lát cắt chạy được trước hết ngày 29/10 thì làm tự luận (§5.14, §6.9, §6.8); lát cắt dọc chưa chạy trước hết 29/10 thì chuyển hẳn toàn bộ phần tự luận sang sau MVP (§1.6) | Mốc |
| **Luồng A, 1–4/11** (duyệt 2026-10-25) | Bài tính toán: R6 phương án B (§6.3); ADR-005 bước 1–3; template ARM theo R10 phương án (c) (§8.1); template Xác suất thống kê. Luồng A không chặn lát cắt dọc. ADR-005 bước 3 và đổi chữ "Gantt" ở I1/I4 đã duyệt 2026-10-25, với điều kiện `tests/test_sandbox_security.py` xanh nguyên vẹn khi đổi code; D6 (§10.1) duyệt 2026-10-26 kèm điều kiện | Kế hoạch |
| **1–4/11: đủ phạm vi dự kiến** | Luồng G1 cho ba môn demo (§1.5); SQLite (§5.13); `RuntimeInfo.capabilities` và UI tương ứng (§5.12, §11.4); adapter LLM qua gateway (§11.5); kiểm `Host` và token (§13.1); tầng 3 với pdfplumber nếu kịp; đóng gói Tauri theo kết quả spike | Kế hoạch |
| **Tới 7/11: dư địa** (duyệt 2026-10-25) | Sửa lỗi phát hiện khi ôn thật; `RELEASE_CHECKLIST.md`; đo p95 (G3) và thời gian bộ nhập PDF | Kế hoạch |

- Thứ tự cắt khi trễ (ghi theo lịch 2026-10-25): tầng 4 (đã park) → tầng 3 → F17 → F16. Không cắt lát cắt dọc. Mốc tự luận 29/10 của lịch này đã được thay bằng lịch 2026-10-26 (30/10–2/11).
- Đề xuất (chờ duyệt): đưa SQLite (§5.13) lên ngay sau mốc 28/10, vì người dùng đầu tiên ôn thật trên app (§1.2) và store in-memory làm mất Vault cùng cặp khái niệm đã xác nhận mỗi lần tắt backend. (đã bị thay: người dùng yêu cầu 2026-10-26 lưu lượt làm bài trong SQLite trong MVP-1; bản zip dùng SQLite, xem đề xuất contract lịch sử)
- **Số đo thật để hiệu chỉnh lịch (2026-10-25):** agent viết spec mất 9–25 phút mỗi đợt; chu kỳ người dùng duyệt một PR mất 1–6 giờ. Thời gian viết code chưa có số đo; điều phối viên bổ sung sau. Ước tính của người viết spec, chưa phải số đo: với chu kỳ duyệt này, mỗi ngày chỉ nên trông vào khoảng 2–4 lần merge, nên các nhánh của lát cắt dọc cần nhỏ và gộp sớm.
- ADR-005 bước 4 và đóng ARCH-001 không có mốc trong lịch trên; làm sau khi luồng A xong bước 1–3.

## 17. Rủi ro & quyết định

| ID | Rủi ro / câu hỏi | Giảm thiểu / quyết định |
|---|---|---|
| R1 | Tỉ lệ chấp nhận seed thấp (RR ≈ 20 %) làm chậm sinh đề | Pool + song song; LLM nhận histogram assert |
| R2 | Quy ước RR/SJF khác nhau giữa giảng viên | Assert `TIE_*` loại trường hợp mơ hồ |
| R3 | VLM đọc sai chữ số viết tay | Dual-pass + self-solve mismatch + người dùng xác nhận |
| R4 | Chưa biết gateway LLM hiện có (của ban tổ chức) nhận ảnh hay không; trước 2026-10-25 nhà cung cấp mặc định cũ không có model thị giác | Cấu hình VLM tách riêng (§11.2); VLM park từ 2026-10-25, kiểm gateway bằng script opt-in trước khi bật lại; app chạy đủ luồng không cần VLM (G5); fallback mock có nhãn (`mock_extractor`) |
| R5 | Tên model của nhà cung cấp thay đổi/ngừng phục vụ | Tên model là cấu hình, không hard-code |
| R6 | Các twin trong cùng batch trùng seed (bước `k·7919` trùng bước re-roll `7919`) ⇒ đề trùng nhau. Đo 2026-10-23 (30 batch × 3 twin RR): 29/30 batch có seed trùng, chỉ 45/90 twin khác nhau (§6.3) | **Đã quyết định** (người dùng chọn phương án B ngày 2026-10-23): tách dải seed, `seed_k = (base_seed + k·7919·40) mod SEED_SPACE`, re-roll `seed_k + j·7919` với `j ∈ [0, 40)`; loại câu trùng theo dấu vân tay, hết dải thì vào `review_tickets` (§6.3, §10). Định nghĩa dấu vân tay và phạm vi so trùng đã duyệt 2026-10-24. Số đo 45/90 là mốc so sánh trước và sau khi sửa. Code chưa sửa |
| R7 | OCR đọc sai dấu tiếng Việt hoặc sai chữ số | Màn hình xem lại bắt buộc trước khi lưu; độ tin cậy theo trang và theo dòng, độ phủ dòng, đối chiếu token số với lượt đọc thứ hai (ADR-008 mục 10–11). Engine chọn ngày 2026-10-24 bằng phép đo trên scan giả lập (ADR-008, mục "Kết quả đo"); phép đo trên scan thật chưa làm |
| R8 | PDF tiếng Việt dùng font mã cũ (TCVN3, VNI) nên lớp chữ là ký tự rác dù trông bình thường | Tầng 1 phát hiện văn bản rác và hạ độ tin cậy để chuyển trang sang OCR (ADR-008) |
| R9 | Model OCR và thư viện ảnh làm bộ cài desktop nặng | Không dùng PyTorch; Tesseract do người dùng tự cài nên không nằm trong gói MVP; Docling để sau MVP như worker tuỳ chọn; ghi kích thước gói vào ADR-008 |
| R10 | Môn demo Kỹ thuật vi xử lý chưa có template tính toán (thanh ghi và cờ N, Z, C, V của CPSR sau một đoạn lệnh) ⇒ rủi ro cho G1 | Viết template mới kèm bộ giải tham chiếu độc lập, test tái lập I4 và property test ≥ 100 seed (§8.4, §15). Lời giải dạng bảng cần `artifacts` đa hình (ADR-005 bước 3). Hợp đồng §8.1 hiện chỉ có một `value` số cho đáp án, nên câu hỏi nhiều giá trị (thanh ghi và bốn cờ) cần quy về một giá trị so sánh được, hoặc đổi contract qua ADR. **Đã chọn (c) cho MCQ4 ngày 2026-10-25** (§8.1); (a) để sau MVP qua một ADR riêng. Template ARM là luồng A, chạy song song và không chặn lát cắt dọc (§16) |
| R11 | Bản SQLite đi kèm Python trên máy người dùng có thể không bật FTS5 | Kiểm `PRAGMA compile_options` khi khởi động; thiếu thì dùng BM25 tự cài (ADR-006 mục 4, §5.13) |
| R12 | Gateway của ban tổ chức chặn User-Agent mặc định (403 "error code: 1010"), có thể không tôn trọng `response_format`, và có thể đổi hành vi hay ngừng phục vụ sau hackathon | Adapter gửi User-Agent riêng, tự parse và kiểm JSON, đếm lần thử và tỉ lệ JSON lỗi (§11.5); LLM là tuỳ chọn nên gateway hỏng không làm hỏng lát cắt dọc (G6); nhà cung cấp đổi được bằng cấu hình |
| R13 | Chấm tự luận bằng n-gram chấm sót ý khi người học diễn đạt khác tài liệu; ngưỡng là giá trị ban đầu (duyệt 2026-10-26), chưa hiệu chỉnh | Chỉ báo "khớp x/y ý", không có điểm tuyệt đối; UI ghi rõ giới hạn và hiện trích dẫn để tự đối chiếu (§5.14); hiệu chỉnh ngưỡng bằng script opt-in; `AnswerJudge` sau MVP |
| R14 | Nội dung tài liệu học đi qua gateway của bên thứ ba (ban tổ chức) khi dùng LLM | LLM là tuỳ chọn; UI nói rõ bên nhận dữ liệu (§13.1); không gửi câu trả lời của người học khi chưa có `AnswerJudge` |
| R15 | MVP-1 chưa xong trước hết 29/10 (§1.5) do chu kỳ duyệt PR 1–6 giờ | Nhánh nhỏ, gộp sớm; luồng A (bài tính toán) không chặn lát cắt; tự luận làm trong 30/10–2/11, sau MVP-1 (chốt 2026-10-26, §16) |
| D1 | Người dùng có được sửa template không? | **Không** trong MVP; chỉ sửa dữ liệu đề qua Gate |
| D2 | Mã HTTP cho xung đột trạng thái | 409 cho xung đột version/duyệt; 400 cho vi phạm luật non-cloneable (đã chốt ở Tuần 2) |
| D3 | So khớp trích dẫn nguyên văn (I3) với chữ có dấu ở dạng tổ hợp (NFD) | **Đã duyệt 2026-10-24:** so sau khi đưa cả hai chuỗi về NFC rồi gộp khoảng trắng; chỉ hai chuỗi tương đương chính tắc mới khớp, nên I3 không bị nới. Áp dụng cho `KnowledgeChunk`, `ConceptPair` và bằng chứng verdict của `AnswerJudge` (§0, §5.8, ADR-006) |
| D4 | 7 điểm "đề xuất" của ADR-008 mục 8–14 và R6 | **Đã duyệt 2026-10-24:** dấu vân tay R6 và phạm vi so trùng (§6.3); `coverage_ratio = min/max` và mẫu số của `ink_outside_text_ratio`; lượt thứ hai dùng PP-OCRv6 small, gọi Tesseract qua stdin/stdout dạng TSV; `HOLOCRON_PDF_OCR_PRIMARY` chỉ nhận `tesseract`, `TEXT_LAYER_MIN=0.85`; tên test và file của 6 test, test 1 kiểm unicharset trên file mẫu; 6 test gắn vào dòng I5 ở CLAUDE.md §9; số dòng tối đa lấy từ `input_schema` thay `MAX_PROCESSES` |
| D5 | Thư viện truy cập SQLite | **Đã duyệt 2026-10-25:** `sqlite3` của thư viện chuẩn, không thêm dependency (§5.13). Comment TODO đã duyệt trong `routers/ingestion.py` (trước khi tách router ở PR #37: `routers/api.py`) vẫn ghi `version_id_col` của SQLAlchemy; sửa cùng nhánh SQLite |
| D6 | Template LLM viết lúc chạy được coi là *đã kiểm chứng* khi nào? | **Đã duyệt 2026-10-26, kèm điều kiện:** bảy lớp K1–K7 ở §10.1 (soạn 2026-10-25, suy ra từ CLAUDE.md §2.1, §2.3, §9 và §15); điều kiện: có ≥ 3 ví dụ có đáp án trích từ tài liệu làm đối chứng độc lập (trả lời câu hỏi mở thứ nhất của §10.1). CLAUDE.md §2.1 yêu cầu "qua đủ các lớp kiểm chứng ở SPECIFICATION.md và được người duyệt". **Còn chờ duyệt:** cách lấy và đối chiếu ba ví dụ đối chứng; ba câu hỏi mở còn lại ở §10.1. Bảng `templates` (§5.13) để sẵn cột `tier`, `verification_json` |
| D7 | 15 điểm "đề xuất (chờ duyệt)" của v0.5 | **Đã duyệt 2026-10-25, trừ điểm 15:** (1) đánh số trang từ 1 (§5); (2) `IngestionItem.cross_check` (§5.4); (3) `ItemLabels` và `PATCH …/labels` (§5.4, §6.7); (4) `TwinBatch.batch_id`, `created_at` (§5.6, §6.7); (5) `DocumentImport`, `PageSummary` (§5.7); (6) `citation_for(subject_code, slot)` (§5.8); (7) đề cương nhập như tài liệu Vault (§5.9); (8) trace dạng union (§5.11); (9) thiết kế SQLite và `sqlite3` (§5.13, D5), `HOLOCRON_DATA_DIR`; (10) giới hạn upload tách hai luồng (§6.0); (11) dấu vân tay câu lý thuyết (§6.3); (12) tên endpoint và nhập bất đồng bộ 202 với hàng đợi FIFO (§6.5); (13) `POST …/pages/{page_number}/items` (§6.5); (14) tên endpoint Vault (§6.6). Điểm (15) "503 khi thiếu key LLM" bị thay bằng chế độ khả năng (§11.4). **Đã quyết thêm (duyệt 2026-10-25, tối):** mã và dạng `detail` của 409 `CapabilityError` và 502 `LLM_UPSTREAM_ERROR` (§6.0); quy ước `HOLOCRON_LLM_*`, điều kiện "LLM đã cấu hình" (§11.2); token và header `X-Holocron-Token`, kiểm `Host` luôn bật (§13.1); `HOLOCRON_DATA_DIR` không đặt thì dùng kho in-memory (§5.13). 503 chỉ còn dùng khi kho SQLite bận (§6.0). **Đã quyết thêm (duyệt 2026-10-26):** danh sách mã `CapabilityReason` (§5.12); mặc định `HOLOCRON_LLM_MODEL_AUTHOR = HOLOCRON_LLM_MODEL_EXTRACT` (§11.2) |
| D8 | Ghi chú "đề xuất" nội dòng nằm ngoài các khối của D7, ví dụ `TheorySlotSpec` và `theory_slots` (§5.9), các field nullable của slot và câu lý thuyết (§5.9, §5.11), `label_status`/`mapping_confidence`/`version` của `KnowledgeChunk` (§5.8), `LineEngine "human"`, `bbox`, `conf` của dòng (§5.7), `mock_extractor` (§5.12), `BlueprintSummary`, `TwinBatchSummary` (§6.7) | **Đã quyết một phần (duyệt 2026-10-25):** `TwinQuestion.template_id` và `topic_key` nullable, `TheoryTrace`, `MisconceptionRef.citation`, `BlueprintSlot.question_kind` (§5.9, §5.11). **Đã quyết thêm (duyệt 2026-10-26):** `TheorySlotSpec` kèm field `format`, `BlueprintCreate.theory_slots`, `BlueprintSlot.source_page_number`, `topic_key`, `self_solve` cho phép `null` (slot lý thuyết), `ReviewTicket.topic_key` cho phép `null` (§5.9, §5.11); field form bắt buộc `chapter_code` của `POST /v1/documents` và nhãn ban đầu của chunk (§5.7, §5.8, §6.5, §12.1); quy tắc sửa dòng, tính lại `review_reasons`, trang `failed` không sửa được và xác nhận trang (§5.7, §6.5); dạng câu lý thuyết do RNG tất định chọn (§5.11, ADR-007 mục 2). **Đã quyết thêm (duyệt 2026-10-26 chiều; trả lời câu hỏi của người viết spec ngày 26/10):** dòng người dùng sửa giữ `engine` và `conf` gốc, nhận `edited_by_human = true`; dòng gõ thêm có `engine = "human"`, `edited_by_human = true`, `conf = 1.0` (§5.7, §6.5); `LineEngine "human"` (§5.7); field `label_status` của `KnowledgeChunk` (§5.8); cùng nội dung, cùng môn, khác `chapter_code` ⇒ 409 (§6.5 bước 3); 409 cho tài liệu nhập trước khi có `chapter_code` ở `GET` tài liệu, `POST` trùng nội dung và hai endpoint xác nhận (§5.7, §6.5); nhập lại sau trang `failed` là "đọc lại trang", để sau MVP (§1.6); đề cương nhiều chương với một `chapter_code` chấp nhận cho MVP (§5.7); `linked_chunk_ids` tính lại theo `subject_code` sau `PATCH …/labels`, không tăng `version` của chunk khác (§12.1, §6.6); tài liệu `failed` chuyển được sang `confirmed`, bước chốt trạng thái chạy cả ở đường `failed` (§5.7, §6.5); `TwinBatchRequest.format` chỉ ghi đè slot tính toán, `ESSAY` ⇒ 422 (§5.6, §6.3); `ExamBlueprint.source_exam_doc_id` nullable kèm validator (§5.9). **Còn chờ người dùng xác nhận:** `mock_extractor` (§5.12); quy tắc lấy mặc định `TheorySlotSpec.format` từ `exam_format` khi null (§5.9, §6.7 bước 5); `mapping_confidence`/`version` của `KnowledgeChunk` (§5.8); `bbox` và `conf` của dòng `text_layer`/`vlm` (§5.7); `TwinBatchSummary` (§6.7); vị trí field `DocumentImport.chapter_code` và cột `documents.chapter_code` (§5.7, §5.13); thứ tự rút dạng/cặp và thứ tự khai báo các dạng câu lý thuyết (§5.11). **Đã quyết thêm (duyệt 2026-10-28, `.claude/state/decisions-2026-10-28.md`):** câu 1: `GET/POST/PATCH /v1/subjects` và `GET /v1/subjects/{code}` (§6.8) đã duyệt và triển khai; câu 2: `POST /v1/blueprints` chỉ nhận slot lý thuyết, `GET /v1/blueprints?subject_code=` và `GET /v1/blueprints/{id}` (§6.7), kèm `BlueprintSummary` (§5.9); câu 14(b): câu lý thuyết vào `TwinBatch` bằng union theo `question_kind` thay cho `TwinQuestion` đa hình và `TheoryTrace`, `TheorySlot` riêng thay cho `BlueprintSlot.question_kind` và các field nullable của slot (§5.9, §5.11, §6.3), nên các mục "đã quyết (duyệt 2026-10-25/26)" ở trên về `TwinQuestion.template_id`/`topic_key` nullable, `TheoryTrace`, `BlueprintSlot.question_kind` và `BlueprintSlot.source_page_number`/`topic_key`/`self_solve` nullable chỉ còn là **mục tiêu sau 29/10 (nợ ADR-004, ARCH-002…ARCH-008)**; `ReviewTicket.topic_key` nullable vẫn đúng và đã có trong code; dấu vân tay câu lý thuyết thay `pair_id` (§6.3); câu 15: `item_ids` khác rỗng ở `POST /v1/blueprints` ⇒ 422 "Blueprint có câu tính toán chưa được hỗ trợ trong bản này" tới sau MVP, chỉ áp cho đường blueprint này; luồng sinh twin tính toán `POST …/twins` giữ nguyên. Hệ quả: kiểm I5 theo item (409) ở `POST /v1/blueprints` (§6.7 bước 2) chuyển vào "mục tiêu sau 29/10"; I5 ở §0 vẫn đúng vì đường này không nhận item. **Duyệt 2026-10-28 (câu 24, 25):** `TheoryTwin.review_status = AUTO_PASS` (§5.11); `source_chunk_id` phải cùng `chapter_code` với slot, khác ⇒ 422 (§6.7 bước 3). **Đề xuất, chờ duyệt (ghi theo code hiện có, không có quyết định riêng; sửa nhãn 2026-10-26 theo review):** giới hạn `topic_keys` ở biên vào (tối đa 32 khoá, mỗi khoá ≤ 128 ký tự, vượt ⇒ 422; §5.8, §6.6); route `/vault?subject=&doc=&page=&tab=` (`tab` là `documents` hoặc `chunks`, theo `holocron/frontend/src/lib/vault.ts`), có trong code từ PR #48; người dùng chưa duyệt riêng danh sách route (§4). **Đề xuất (chờ duyệt), chưa có quyết định:** khi bấm trích dẫn (§4 bước 7, §12.2), hiện tên tài liệu thay `doc_id`: tên là `DocumentMeta.title` (người dùng đặt, mặc định tên file bỏ đuôi, §5.8), dự phòng `DocumentImport.filename` (§5.7); frontend lấy tên từ `Citation.doc_id` bằng cách tra `GET /v1/documents?subject_code=` hoặc `GET /v1/documents/{document_id}` (§6.5), không thêm field vào `Citation`. Câu chữ đã duyệt ở §4 bước 7 và §12.2 vẫn là "kèm `doc_id` và `page_number`". **Đề xuất (chi tiết) mới, chờ duyệt (viết 2026-10-26 chiều cho các hướng đã duyệt):** chi tiết luật "đúng một đáp án" `theory_mcq4_v3`: thay hay cộng thêm luật ký tự v2, xử lý ký tự `Cf`, dãy token rỗng, khoá khái niệm (§5.11; câu 1b); nguồn `source_exam_doc_id` khi có item (§5.9); tài liệu không có trang nào có văn bản không chuyển `confirmed` (§5.7); `detail` của 409 "chưa có `chapter_code`" và của 503 "app đang tắt" (§5.7, §6.0); toàn bộ chi tiết kiểm `Origin` (§13.1, §6.0); tên, kiểu, nguồn và quy tắc của `RuntimeInfo.storage_mode`, `RuntimeInfo.llm_data_recipient` và biến `HOLOCRON_LLM_DATA_RECIPIENT` (§5.12, §11.2) |
| D9 | Quyết định ngày 2026-10-25 và 2026-10-26 lệch câu chữ ADR đã *Chấp nhận* | **Đã sửa câu chữ ADR (duyệt 2026-10-25):** ADR-005 (Hệ quả: `format` thêm `ESSAY`; bước 3 duyệt có điều kiện); ADR-006 và ADR-007 (nhà cung cấp LLM do cấu hình quyết định thay nhà cung cấp mặc định cũ); ADR-007 (tiêu đề, mục 2 khoá sắp xếp `pool_sha256`, mục 5 tách 5a chấm offline / 5b `AnswerJudge` sau MVP); ADR-008 (mục 1 pdfplumber, mục 6 ghi chú `find_tables`, mục 8 lệnh Tesseract, mục 13 `page_number`, mục 14 `OCR_CHARSET_ALLOWED_MISSING`, câu chữ VLM); ADR-004 (đường dẫn sau khi tách router). Trạng thái các ADR không đổi. **Đã sửa câu chữ ADR (duyệt 2026-10-26 chiều):** ADR-006 mục 2 từng ghi LLM đề xuất `chapter_code` theo heading; nay ghi `chapter_code` lấy từ field bắt buộc khi nhập tài liệu (một file = một chương, duyệt 2026-10-26), LLM (nếu có) chỉ đề xuất đổi chương cho từng chunk và người dùng xác nhận (§5.7, §5.8, §6.5, §12.1). ADR-007 mục 1–2 thêm hướng luật "đúng một đáp án" của `theory_mcq4_v3`; chi tiết chờ duyệt câu 1b (§5.11). **Đã sửa câu chữ ADR (duyệt 2026-10-26, câu 19 và lịch MVP-1):** ADR-007 mục 5a (tham số ban đầu, bỏ n-gram thuộc `term`, `term` trong tái lập, lịch tự luận 30/10–2/11). Trạng thái các ADR không đổi. Không còn chỗ lệch nào đã biết giữa spec và ADR |
| D10 | Câu hỏi mở về chế độ khả năng và adapter | (c) **Đã quyết (duyệt 2026-10-25):** dual-pass không đặt `temperature`; hai lượt độc lập nhờ khác prompt (§7.1). Còn đề xuất, chờ duyệt: (a) giữ `demo_mode` nhưng đổi chữ trên UI thành "chạy offline, không có LLM" (§5.12); (b) thêm khả năng `label_suggestion_llm` (§11.4) |
| D11 | Câu chữ I4 ở §0 chưa nhắc tái lập của bộ chấm tự luận; I3 chưa nhắc các ý của câu tự luận | **Đã quyết (duyệt 2026-10-25), chỉ phần câu chữ §0:** I4 thêm "Chấm tự luận offline: cùng `grader_version`, ngưỡng, các ý và câu trả lời (NFC) ⇒ cùng kết quả"; cơ chế I3 thêm "các ý của câu ESSAY là chuỗi con (sau NFC) của `definition_quote`"; I1 thêm ngoại lệ cho bộ tính điểm chấm câu trả lời của người học. Test tái lập I4 của bộ chấm `tests/test_essay_grader.py` (§15) đã có trong bảng CLAUDE.md §9, ở dòng I4, ghi "khi làm essay" (file test chưa có, viết cùng phần tự luận). (a) **Đã đóng (duyệt 2026-10-26 chiều):** dòng test cưỡng chế ở CLAUDE.md §9 cho ngoại lệ I1 (dòng I1: `tests/test_essay_grader.py`, kết quả chấm chỉ gồm `matched_count`/`total_points` và danh sách ý) và quy tắc ESSAY của I3 (dòng I3: `tests/test_essay_grader.py`, mọi ý là chuỗi con sau NFC của `definition_quote`) đã được duyệt và có trong CLAUDE.md §9, cả hai ghi "khi làm essay". **Đã đóng (duyệt 2026-10-26):** (b) chọn phương án (a): bước 2 của phần chấm ở §5.14 (bỏ n-gram thuộc tên khái niệm `term`) được duyệt, và câu chữ I4 ở §0 thêm `term` ("cùng `grader_version`, ngưỡng, `term`, các ý và câu trả lời (NFC) ⇒ cùng kết quả"); (c) chọn phương án (a): câu chữ cơ chế I3 ở §0 sửa từ "(sau NFC)" thành câu đầy đủ "các ý của câu ESSAY là chuỗi con của `definition_quote` theo cùng quy tắc so khớp: đưa cả hai chuỗi về NFC rồi gộp khoảng trắng, sau đó mới kiểm chuỗi con", khớp validator ở §5.14 và ADR-007 mục 5a. §0 chỉ đổi đúng hai chỗ câu chữ này. **Còn chờ:** câu chữ hai dòng essay ở CLAUDE.md §9 (I1, I3) chờ câu 23 (evening-questions): dòng I3 ghi "chuỗi con sau NFC" trong khi §0 nay ghi "NFC rồi gộp khoảng trắng"; dòng I1 ghi kết quả chấm "chỉ gồm `matched_count`/`total_points` và danh sách ý" trong khi `EssayGradeResult` (§5.14) còn có `grader_version`, `thresholds` và `ngram_coverage` của từng ý. Spec không sửa CLAUDE.md; cần trước khi làm essay (30/10) |
| D12 | Bỏ nhà cung cấp mặc định cũ trong code | **Đã quyết (duyệt 2026-10-25):** người dùng đồng ý sửa assert hai giá trị mặc định cũ (địa chỉ endpoint và tên model) trong `test_defaults_when_nothing_is_configured` và assert số khoá của `GET /v1/runtime` trong `tests/test_llm_config.py`, để khớp §11.2 và §5.12 khi đổi code. Ngoài các assert đó, test sẵn có giữ nguyên (CLAUDE.md §7) |
| D13 | Kiểm `Host` luôn bật và `TestClient` | **Đã quyết (duyệt 2026-10-25):** danh sách host cho phép lấy từ provider `get_allowed_hosts`, middleware `LocalAccessMiddleware` tra `app.dependency_overrides` trước khi gọi provider; test override bằng fixture `autouse` trong `tests/conftest.py`, không sửa test sẵn có. Fixture này tác động cả app dựng lại trong test, không chỉ `app` của `routers/api.py`: ngoài override trên `app.dependency_overrides`, fixture thay `access.LocalAccessMiddleware` bằng lớp con thêm `testserver` làm mặc định của provider host (app mới có `dependency_overrides` riêng nên override trên `app` không áp cho nó); override riêng của app mới vẫn thắng; KHÔNG đưa `testserver` vào danh sách cho phép mặc định (§13.1) |
| D14 | Cột cơ chế của I1 ở §0 ghi "`TwinQuestion` chỉ được dựng từ `SandboxResult`", trong khi câu lý thuyết nối vào luồng twin (§5.11, §17 D8) không dựng từ `SandboxResult` mà mang `TheoryTrace` | **Đã đóng (duyệt 2026-10-26 chiều).** Cột cơ chế của I1 ở §0 nay ghi: "LLM chỉ được sinh *mã template*; `TwinQuestion` câu tính toán chỉ được dựng từ `SandboxResult` (§9, §10); câu lý thuyết chỉ được dựng từ cặp khái niệm đã xác nhận qua bộ sinh tất định (`TheoryTrace`, ADR-007), không chứa con số do LLM viết." Diff đã duyệt chỉ thay vế "`TwinQuestion` chỉ được dựng từ `SandboxResult` (§9, §10)"; vế đầu giữ nguyên. Phần còn lại của §0 không đổi |

## 18. Phụ lục

### A. Danh mục misconception

| Topic | Mã | Ý nghĩa |
|---|---|---|
| RR | `RR_TREATED_AS_FCFS` | Bỏ qua quantum, chạy hết burst theo thứ tự đến |
| RR | `WAIT_EQUALS_FIRST_RESPONSE` | Lấy response time thay cho waiting time |
| RR, SJF | `WAIT_CONFUSED_WITH_TURNAROUND` | Lấy turnaround mà quên trừ burst |
| SJF | `SJF_IGNORES_ARRIVAL` | Sắp theo burst mà bỏ qua thời điểm đến |
| SJF | `SJF_COMPUTED_AS_SRTF` | Cho phép ngắt (SRTF) trong khi đề là non-preemptive |
| Câu lý thuyết (`THEORY_MISCONCEPTIONS`, ADR-007 mục 2) | `THEORY_SAME_CHAPTER_CONCEPT` | Nhầm với một khái niệm khác cùng chương |
| Câu lý thuyết (`THEORY_MISCONCEPTIONS`, ADR-007 mục 2) | `THEORY_SAME_SUBJECT_CONCEPT` | Nhầm với một khái niệm khác cùng môn, khác chương |

### B. JSON Schema
Sinh bằng `Model.model_json_schema()` cho mọi model ở §5; Frontend PHẢI đồng bộ `types/holocron.ts` trong cùng PR khi model đổi.

### C. ADR
- `docs/adr/001-architecture-core.md` — Kiến trúc hai tầng và phân tầng package.
- `docs/adr/002-sandbox-security.md` — Sandbox tiến trình con thay vì WASM; Job Object trên Windows.
- `docs/adr/003-data-pipeline.md` — Dual-pass + Ingestion Gate; trích dẫn bằng `verbatim_quote` thay vì bounding box.
- `docs/adr/004-engineering-discipline.md` — Kỷ luật kỹ thuật, quản trị phát hành và sổ nợ kỹ thuật (sổ nợ bổ sung BE-005, BE-006, SEC-001, PKG-002 ngày 2026-10-26 chiều, PKG-003 đề xuất 2026-10-28, chờ người dùng duyệt).
- `docs/adr/005-topic-agnostic-question-pipeline.md` — Pipeline câu hỏi không phụ thuộc topic; kế hoạch trả nợ ARCH-001 (*Chấp nhận*; bước 3 và câu chữ I1/I4 duyệt có điều kiện 2026-10-25).
- `docs/adr/006-multi-subject-knowledge-vault.md` — Knowledge Vault đa môn, tách từ tiếng Việt cho BM25 và `CitationProvider` (*Chấp nhận*; câu chữ nhà cung cấp LLM sửa theo quyết định 2026-10-25, mục 2 về `chapter_code` sửa theo quyết định chiều 2026-10-26, §17 D9).
- `docs/adr/007-theory-questions-and-answer-grading.md` — Câu lý thuyết sinh tất định từ Vault; chấm tự luận offline (MVP nếu còn thời gian), `AnswerJudge` sau MVP; câu chữ I2/I4 cho câu lý thuyết (*Chấp nhận*; tiêu đề, mục 1–3, 5 và Hệ quả sửa theo quyết định 2026-10-25; mục 2 sửa theo quyết định 2026-10-26; mục 1–2 sửa theo quyết định chiều 2026-10-26 (hướng `theory_mcq4_v3`, chi tiết chờ duyệt); mục 5a sửa theo quyết định 2026-10-26 (câu 19, lịch MVP-1), §17 D9).
- `docs/adr/008-tiered-pdf-importer.md` — Bộ nhập PDF theo tầng, lựa chọn thư viện và biện pháp an toàn (*Chấp nhận*; engine OCR chốt ngày 2026-10-24: Tesseract `vie` chính, RapidOCR đối chiếu số; các điểm đề xuất ở mục 8–14 duyệt 2026-10-24; mục 1, 6, 8, 13, 14 và câu chữ VLM sửa theo quyết định 2026-10-25, §17 D9).
