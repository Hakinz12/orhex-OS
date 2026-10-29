import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "ORHEX OS — Your World. Understood.",
    template: "%s · ORHEX OS",
  },
  description:
    "An intelligent personal operating system that remembers relevant context, understands your world, and helps turn understanding into action.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <a
          href="#main-content"
          className="sr-only z-[100] rounded-lg bg-[#d3e8f7] px-4 py-3 text-[#122234] focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
        >
          Skip to content
        </a>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
