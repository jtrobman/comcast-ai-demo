import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "SMB Resolution Copilot",
  description: "Comcast Business-inspired AI support operations demo",
  robots: {
    index: false,
    follow: false,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
