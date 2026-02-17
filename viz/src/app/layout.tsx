import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Syke — Agentic Memory",
  description:
    "Your digital footprint — code, conversations, commits, emails — synthesized into a living model. Daemon-synced. Every AI tool you use, knows you.",
  openGraph: {
    title: "Syke — Agentic Memory",
    description:
      "Your digital footprint — code, conversations, commits, emails — synthesized into a living model. Daemon-synced. Every AI tool you use, knows you.",
    siteName: "Syke",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Syke — Agentic Memory",
    description:
      "Your digital footprint — code, conversations, commits, emails — synthesized into a living model. Daemon-synced. Every AI tool you use, knows you.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${inter.variable} ${jetbrains.variable} font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
