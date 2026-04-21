import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Indonesia PE Deal Sourcer",
  description:
    "Identify manufacturing and industrial acquisition targets in Indonesia by postcode.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
