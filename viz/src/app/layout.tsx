import type { Metadata } from "next";
import { Playfair_Display, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const playfair = Playfair_Display({
  variable: "--font-playfair",
  subsets: ["latin"],
  weight: ["400", "700"],
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  weight: ["100", "400", "500", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Syke — Agentic Memory",
  description:
    "Your digital footprint — code, conversations, commits, emails — synthesized into a living model. Every AI tool you use, knows you.",
  openGraph: {
    title: "Syke — Agentic Memory",
    description: "Your digital footprint synthesized into a living model. Every AI tool you use, knows you.",
    siteName: "Syke",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Syke — Agentic Memory",
    description: "Your digital footprint synthesized into a living model. Every AI tool you use, knows you.",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${playfair.variable} ${jetbrains.variable} font-mono-term antialiased`}>
        <div className="scanlines" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
