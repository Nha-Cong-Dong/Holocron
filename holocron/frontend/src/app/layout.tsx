import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/AppShell";

import "./globals.css";

export const metadata: Metadata = {
  title: "Holocron",
  description:
    "Ôn thi từ tài liệu môn học của bạn: kho tri thức có trích dẫn nguyên văn và câu hỏi luyện tập có kiểm chứng.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
