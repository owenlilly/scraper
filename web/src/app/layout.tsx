import type { Metadata, Viewport } from "next";
import { Literata, Schibsted_Grotesk } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";

// Literata, made for long reading, carries the agent's prose and the headings; Schibsted Grotesk carries the interface.
const read = Literata({ subsets: ["latin"], variable: "--font-read", display: "swap" });
const ui = Schibsted_Grotesk({ subsets: ["latin"], variable: "--font-ui", display: "swap" });

export const metadata: Metadata = { title: { default: "Page Chat", template: "%s | Page Chat" } };
export const viewport: Viewport = { colorScheme: "light dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${read.variable} ${ui.variable}`}>
      <body>{children}</body>
    </html>
  );
}
