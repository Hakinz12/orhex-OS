"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  BellIcon,
  CloseIcon,
  HomeIcon,
  InsightsIcon,
  MarkIcon,
  MemoryIcon,
  MenuIcon,
  SearchIcon,
  SettingsIcon,
  ShieldIcon,
  SparkIcon,
  TimelineIcon,
} from "@/components/icons";

const navigation = [
  { label: "Home", href: "/", Icon: HomeIcon },
  { label: "AI Workspace", href: "/workspace", Icon: SparkIcon },
  { label: "Memory", href: "/memory", Icon: MemoryIcon },
  { label: "Timeline", href: "/timeline", Icon: TimelineIcon },
  { label: "Insights", href: "/insights", Icon: InsightsIcon },
];
const searchableRoutes = [
  ...navigation.map(({ label, href }) => ({ label, href })),
  { label: "Settings", href: "/settings" },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [search, setSearch] = useState("");
  const matches = useMemo(
    () => searchableRoutes.filter(({ label }) => label.toLowerCase().includes(search.trim().toLowerCase())),
    [search],
  );

  if (pathname === "/signin") return <>{children}</>;

  function searchBox(mobile = false) {
    return (
      <div className={`relative ${mobile ? "" : "hidden w-full max-w-[430px] sm:block"}`}>
        <SearchIcon className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[#6f7f90]" />
        <input
          type="search"
          aria-label="Find a workspace page"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && matches[0]) {
              router.push(matches[0].href);
              setSearch("");
            }
            if (event.key === "Escape") setSearch("");
          }}
          placeholder="Find a workspace page"
          className={`h-10 w-full rounded-xl border border-[#202a35] bg-[#0f151d] pl-10 pr-4 text-[12px] text-[#e8edf2] placeholder:text-[#68788a] focus:border-[#536b80] focus:outline-none ${mobile ? "" : "pr-14"}`}
        />
        {!mobile && <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded border border-[#2a3541] px-1.5 py-0.5 text-[9px] text-[#778697]">↵</kbd>}
        {search.trim().length > 0 && (
          <div
            id="workspace-search-results"
              role="region"
              aria-label="Workspace page search results"
              className="absolute left-0 right-0 top-[calc(100%+8px)] z-40 overflow-hidden rounded-xl border border-[#2b3743] bg-[#111923] p-1.5 shadow-2xl"
          >
            {matches.length ? matches.map((match) => (
              <Link
                key={match.href}
                href={match.href}
                onClick={() => setSearch("")}
                className="block rounded-lg px-3 py-2.5 text-[12px] text-[#b8c5d0] transition-colors hover:bg-[#1b2733] hover:text-white"
              >
                {match.label}
              </Link>
            )) : <p className="px-3 py-3 text-[11px] text-[#8794a2]">No matching workspace pages.</p>}
          </div>
        )}
      </div>
    );
  }

  const navLinks = (mobile = false) => (
    <nav aria-label="Main navigation" className="space-y-1">
      <p className="eyebrow mb-3 px-3 pt-3">Workspace</p>
      {navigation.map(({ label, href, Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            onClick={() => mobile && setMobileOpen(false)}
            className={`group flex min-h-11 items-center gap-3 rounded-xl px-3 text-[13px] font-medium transition-colors ${
              active
                ? "bg-[#1b2734] text-[#d3e8f7]"
                : "text-[#8998a8] hover:bg-[#141d27] hover:text-[#eef3f8]"
            }`}
          >
            <Icon className={`h-[18px] w-[18px] ${active ? "text-[#b7d7f0]" : "text-[#718193] group-hover:text-[#b7d7f0]"}`} />
            {label}
          </Link>
        );
      })}
      <p className="eyebrow mb-3 mt-8 px-3 pt-3">Personal</p>
      <Link
        href="/settings"
        aria-current={isActive(pathname, "/settings") ? "page" : undefined}
        onClick={() => mobile && setMobileOpen(false)}
        className={`group flex min-h-11 items-center gap-3 rounded-xl px-3 text-[13px] font-medium transition-colors ${
          isActive(pathname, "/settings")
            ? "bg-[#1b2734] text-[#d3e8f7]"
            : "text-[#8998a8] hover:bg-[#141d27] hover:text-[#eef3f8]"
        }`}
      >
        <SettingsIcon className="h-[18px] w-[18px] text-[#718193] group-hover:text-[#b7d7f0]" />
        Settings
      </Link>
    </nav>
  );

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[252px] flex-col border-r border-[#1c2530] bg-[#0b1017] lg:flex">
        <Link href="/" className="flex h-[84px] items-center gap-3 px-6" aria-label="ORHEX home">
          <span className="flex h-10 w-10 items-center justify-center rounded-[14px] border border-[#344554] bg-[#17222d] text-[#c5dced]">
            <MarkIcon className="h-6 w-6" />
          </span>
          <span>
            <span className="font-display block text-[16px] font-extrabold tracking-[0.16em] text-[#f0f3f6]">ORHEX</span>
            <span className="mt-0.5 block text-[10px] tracking-[0.04em] text-[#8392a2]">Your World. Understood.</span>
          </span>
        </Link>
        <div className="mx-5 h-px bg-[#1c2530]" />
        <div className="flex-1 px-4 py-5">{navLinks()}</div>
        <div className="mx-4 mb-4 rounded-[15px] border border-[#26323e] bg-[#111923] p-4">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-[#dce4ec]">
            <ShieldIcon className="h-5 w-5 text-[#b7d7f0]" />
            Private by design
          </div>
          <p className="mt-2 text-[11px] leading-5 text-[#8493a3]">
            Private operations use the session verified by the ORHEX backend. Manage access in Settings.
          </p>
          <span className="mt-3 inline-flex rounded-md border border-[#303d49] px-2 py-1 text-[9px] font-semibold uppercase tracking-[0.1em] text-[#9ba9b7]">
            Protected session
          </span>
        </div>
        <div className="border-t border-[#1c2530] px-5 py-4">
          <p className="text-[10px] text-[#647384]">ORHEX OS · Private intelligence</p>
        </div>
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-black/70"
            aria-label="Close navigation menu"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-[min(84vw,310px)] flex-col border-r border-[#26323e] bg-[#0b1017] shadow-2xl">
            <div className="flex h-[76px] items-center justify-between px-5">
              <Link href="/" onClick={() => setMobileOpen(false)} className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#344554] bg-[#17222d] text-[#c5dced]"><MarkIcon className="h-5 w-5" /></span>
                <span className="font-display text-sm font-extrabold tracking-[0.15em]">ORHEX</span>
              </Link>
              <button className="icon-button" aria-label="Close menu" onClick={() => setMobileOpen(false)}><CloseIcon className="h-5 w-5" /></button>
            </div>
            <div className="flex-1 px-4 py-4">{navLinks(true)}</div>
            <p className="px-5 pb-5 text-xs text-[#738293]">Your World. Understood.</p>
          </aside>
        </div>
      )}

      <div className="min-h-screen lg:pl-[252px]">
        <header className="sticky top-0 z-20 border-b border-[#1c2530] bg-[#090c12]/95 backdrop-blur">
          <div className="mx-auto flex h-[68px] max-w-[1600px] items-center gap-3 px-4 sm:px-7 lg:px-10">
            <button className="icon-button lg:hidden" aria-label="Open navigation menu" onClick={() => setMobileOpen(true)}>
              <MenuIcon className="h-5 w-5" />
            </button>
            <Link href="/" className="flex shrink-0 items-center gap-2 lg:hidden" aria-label="ORHEX home">
              <span className="flex h-8 w-8 items-center justify-center rounded-[11px] border border-[#344554] bg-[#17222d] text-[#c5dced]">
                <MarkIcon className="h-[18px] w-[18px]" />
              </span>
              <span className="font-display text-[11px] font-extrabold tracking-[0.13em] text-[#e7edf2]">ORHEX</span>
            </Link>
            {searchBox()}
            <div className="ml-auto flex items-center gap-2">
              <span className="hidden items-center gap-2 rounded-full border border-[#26313d] px-3 py-1.5 text-[10px] text-[#9aa8b6] md:inline-flex">
                <span className="status-dot" data-status="unconfigured" />
                Secure session
              </span>
              <button className="icon-button" aria-label="Notifications unavailable" title="Notifications will be available when your account is connected" disabled>
                <BellIcon className="h-[18px] w-[18px]" />
              </button>
              <Link href="/settings" className="icon-button" aria-label="Open session settings" title="Session settings">
                <ShieldIcon className="h-[18px] w-[18px]" />
              </Link>
            </div>
          </div>
          <div className="px-4 pb-2 sm:hidden">{searchBox(true)}</div>
        </header>
        <main id="main-content" className="mx-auto w-full max-w-[1600px] px-4 py-7 sm:px-7 sm:py-9 lg:px-10 lg:py-10">
          {children}
        </main>
      </div>
    </div>
  );
}
