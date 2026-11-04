import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Repost Radar", description: "SmartEmploy lead engine and CRM", robots: { index: false } };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB">
      <body>{children}</body>
    </html>
  );
}
