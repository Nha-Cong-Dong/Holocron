// Chạy trong <head> trước khi vẽ để không nháy màu (design-handoff/README.md mục 3): đặt `data-theme` và
// `data-motion` trên <html> từ lựa chọn đã lưu. Khoá lưu khớp `src/lib/theme.ts`; kho bị chặn thì giữ mặc định.
(function () {
  try {
    var root = document.documentElement;
    var theme = localStorage.getItem("holocron.theme");
    if (theme === "light" || theme === "dark") root.dataset.theme = theme;
    if (localStorage.getItem("holocron.motion") === "reduce") root.dataset.motion = "reduce";
  } catch (e) {}
})();
