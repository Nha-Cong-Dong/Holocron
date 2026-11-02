import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/AppShell";

import "@fontsource/lexend/vietnamese-400.css";
import "@fontsource/lexend/vietnamese-500.css";
import "@fontsource/lexend/vietnamese-600.css";
import "@fontsource/lexend/latin-400.css";
import "@fontsource/lexend/latin-500.css";
import "@fontsource/lexend/latin-600.css";
import "@fontsource/newsreader/vietnamese-400.css";
import "@fontsource/newsreader/vietnamese-400-italic.css";
import "@fontsource/newsreader/latin-400.css";
import "@fontsource/newsreader/latin-400-italic.css";
import "./tokens.css";
import "./motion.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "Holocron",
  icons: { icon: "/brand/icon-16.svg" },
  description:
    "Ôn thi từ tài liệu môn học của bạn: kho tri thức có trích dẫn nguyên văn và câu hỏi luyện tập có kiểm chứng.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `data-theme` mặc định "auto"; script trong <head> đổi theo lựa chọn đã lưu trước khi vẽ nên React thấy thuộc
    // tính khác lúc hydrate, `suppressHydrationWarning` chỉ áp cho chính thẻ <html>.
    <html lang="vi" data-theme="auto" suppressHydrationWarning>
      <head>
        {/* Script đồng bộ, cố ý chặn vẽ cho tới khi đặt xong theme (`public/theme-init.js`). */}
        <script src="/theme-init.js" />
      </head>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
