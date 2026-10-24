# Release checklist

Hoàn tất đủ 10/10 mục cho đúng commit sẽ tag trước `v1.0.0-rc`. Với pre-release sớm hơn (`alpha`, `beta`), ghi rõ mục nào chưa áp dụng được và lý do trong ghi chú phát hành; không bỏ qua mục 1, 2, 6 và 7.

1. **CI xanh trên Linux và Windows** cho đúng commit sẽ tag: `pytest`, `ruff check`, `ruff format --check`, `mypy --strict`, `biome ci`, `tsc`, `next build`.
2. **I1–I5 đều có test cưỡng chế** (bảng ở `.claude/CLAUDE.md` §9) và không còn finding mức Cao mở.
3. **Cold start** backend ≤ 5 s, và frontend khởi động được từ clean checkout theo README (không cache).
4. **Hiệu năng:** Twin p95 ≤ 3 s (không tính LLM) và Gate PATCH p95 ≤ 500 ms, đo trên máy Windows dùng demo (`python scripts/release_bench.py`).
5. **OCR/VLM:** dual-pass chạy trên ≥ 5 ảnh đề thật (≥ 2 ảnh mờ hoặc ghi tay; ghi rõ độ phân giải tối thiểu đã thử); mọi ô sai đều bị Gate gắn cờ (recall 100 % trên tập kiểm); tỉ lệ ô đọc đúng ghi vào release notes.
6. **Sandbox:** crash/timeout/OOM = 0/200 seed cho mỗi template đã kiểm chứng, gồm template của các môn demo (`SPECIFICATION.md` §1.5) và template RR, SJF dùng làm fixture; suite bảo mật xanh trên cả hai hệ điều hành; whitelist không bị nới.
7. **Tái lập I4:** `python -m scripts.run_walkthrough` giống từng byte trên Linux và Windows; `git diff --exit-code -- holocron/backend/examples` sạch.
8. **Offline/local fallback:** tắt mạng và không đặt `HOLOCRON_VLM_*` thì dùng `MockDualPassExtractor` và UI hiện nhãn "chế độ demo"; không có request ra ngoài; không đặt key LLM thì dùng template precomputed; không key nào lọt vào log hoặc response.
9. **Golden Path** chạy từ đầu đến cuối ≥ 3 lần từ clean checkout (≤ 10 phút): upload → sửa ô → approve → blueprint → 3 Twin + Gantt cho cả RR và SJF, `review_tickets` rỗng.
10. **Phát hành:** mục CHANGELOG đúng 5 nhóm, `scripts/check_versions.py` xanh, tag SemVer, release notes sinh từ CHANGELOG, có video demo quay sẵn và dữ liệu mẫu offline làm phương án dự phòng.

> Trạng thái hiện tại (v0.1.0-alpha): các mục 5, 8, 9 chưa áp dụng được vì chưa có VLM thật, chưa có `POST /v1/blueprints`, chưa có template SJF và chưa có nhãn "chế độ demo" trên UI. Các mục này là điều kiện của `v0.2.0-beta` và `v1.0.0-rc`.
