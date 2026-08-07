import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Gipfel Trading Arena",
  description: "Business competition stock trading platform"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
