import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "企业智能工单系统",
  description: "AI-powered enterprise ticket automation with Generative UI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
