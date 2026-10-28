import type { Metadata } from "next";
import Link from "next/link";
import "../styles/globals.css";

export const metadata: Metadata = {
  title: "ORHREX OS — Intelligence That Anticipates",
  description: "Memory-powered personal AI operating system for Tatum x Walrus Total Recall 2026",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-[#070b14] text-slate-100 antialiased selection:bg-brand-500 selection:text-white">
        <header className="sticky top-0 z-50 border-b border-slate-800/80 bg-[#070b14]/90 backdrop-blur-md">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <Link href="/" className="flex items-center gap-2 group">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 via-purple-500 to-sky-400 font-bold text-white shadow-lg shadow-indigo-500/20">
                  Ω
                </div>
                <div>
                  <span className="text-lg font-bold tracking-tight text-white group-hover:text-indigo-400 transition-colors">
                    ORHREX <span className="text-xs font-semibold px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">OS</span>
                  </span>
                  <p className="text-[10px] text-slate-400 tracking-wider uppercase font-medium">Intelligence That Anticipates</p>
                </div>
              </Link>
            </div>

            <nav className="hidden md:flex items-center gap-1 text-sm font-medium">
              <Link
                href="/"
                className="px-3 py-1.5 rounded-md text-slate-200 hover:text-white hover:bg-slate-800/60 transition-colors"
              >
                Dashboard
              </Link>
              <Link
                href="/chat"
                className="px-3 py-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
              >
                Contextual Chat
              </Link>
              <Link
                href="/memories"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
              >
                <span>Memory Centre</span>
                <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20">Walrus</span>
              </Link>
              <Link
                href="/sui"
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
              >
                <span>Sui Intelligence</span>
                <span className="text-[10px] font-mono px-1 py-0.2 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">Tatum</span>
              </Link>
            </nav>

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 rounded-full bg-slate-900 border border-slate-800 px-3 py-1 text-xs text-slate-400">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <span className="font-mono text-[11px] text-slate-300">Milestone 1 Active</span>
              </div>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">
          {children}
        </main>

        <footer className="mt-auto border-t border-slate-800/60 py-6 text-center text-xs text-slate-500">
          <div className="mx-auto max-w-7xl px-4 flex flex-col sm:flex-row items-center justify-between gap-2">
            <p>ORHREX OS &mdash; Tatum x Walrus Total Recall 2026 Hackathon (Smart Agents for Real Life)</p>
            <p className="font-mono text-[11px]">Decentralized Persistence &bull; Read-Only Sui Intelligence</p>
          </div>
        </footer>
      </body>
    </html>
  );
}

