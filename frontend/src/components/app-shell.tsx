"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Bot, Check, ChevronsUpDown, Database, Gauge, Globe, LayoutDashboard,
  LogOut, Menu, MessageSquare, MessagesSquare, Plug, Plus, Search, Settings, ShieldCheck, X,
} from "lucide-react";
import { ThemeToggle } from "./theme-toggle";
import { Avatar, Input } from "./ui";
import { GateButton } from "./gate";
import { cn } from "@/lib/utils";
import { useAuth, useWorkspace } from "@/lib/auth";
import { ROLE_LABEL, ROLE_ORDER, type Role } from "@/lib/permissions";
import { TEAMS } from "@/lib/iam";
import { toast, joinWorkspace } from "@/lib/store";
import { ORG } from "@/lib/org";

type NavItem = { href: string; label: string; icon: React.ComponentType<{ className?: string }> };
const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Overview", items: [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    { href: "/agents", label: "Agents", icon: Bot },
    { href: "/playground", label: "Playground", icon: MessageSquare },
  ] },
  { group: "Build", items: [
    { href: "/knowledge", label: "Knowledge", icon: Database },
    { href: "/tools", label: "Tools & MCP", icon: Plug },
    { href: "/channels", label: "Channels", icon: MessagesSquare },
  ] },
  { group: "Observe", items: [
    { href: "/evaluations", label: "Evaluations", icon: Gauge },
    { href: "/runs", label: "Runs", icon: Activity },
    { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  ] },
];

/** Dropdown that closes on outside-click / Escape. */
function useDismiss(onClose: () => void) {
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);
  return ref;
}

const roleTone: Record<Role, string> = {
  viewer: "bg-elevated text-ink-2",
  builder: "bg-accent-soft text-accent",
  publisher: "bg-s7/15 text-s7",
  admin: "bg-good/12 text-good",
};

function Brand() {
  const Logo = ORG.logo;
  return (
    <Link href="/dashboard" className="flex items-center gap-2.5">
      <span className="inline-flex size-8 items-center justify-center rounded-lg bg-accent text-accent-ink">
        <Logo />
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-ink">{ORG.productName}</span>
    </Link>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex-1 space-y-6 px-3 py-4">
      {NAV.map((section) => (
        <div key={section.group}>
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{section.group}</p>
          <ul className="space-y-0.5">
            {section.items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(item.href + "/");
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    className={cn(
                      "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-150",
                      active ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-elevated hover:text-ink",
                    )}
                  >
                    {active && <span className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-r-full bg-accent" />}
                    <Icon className="size-4 shrink-0" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function WorkspaceSwitcher() {
  const { workspaces, joinable, workspace, setWorkspace } = useWorkspace();
  const { role } = useAuth();
  const [open, setOpen] = React.useState(false);
  const ref = useDismiss(() => setOpen(false));
  const current = workspace;

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 rounded-lg border border-border bg-elevated px-2.5 py-2 text-left transition-colors hover:bg-overlay"
      >
        <span className="inline-flex size-7 items-center justify-center rounded-md bg-s7/20 text-xs font-bold text-s7">
          {current?.short ?? "-"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">{current?.name ?? "No workspace"}</span>
          <span className="block truncate text-[11px] text-muted">Your role: {ROLE_LABEL[role]}</span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 text-muted" />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 rounded-lg border border-border bg-surface p-1 shadow-lg">
          {workspaces.map((w) => (
            <button
              key={w.id}
              onClick={() => { setWorkspace(w.id); setOpen(false); }}
              className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-elevated"
            >
              <span className="inline-flex size-6 items-center justify-center rounded-md bg-s7/20 text-[10px] font-bold text-s7">{w.short}</span>
              <span className="min-w-0 flex-1 truncate text-sm text-ink">{w.name}</span>
              {w.id === current?.id && <Check className="size-3.5 text-accent" />}
            </button>
          ))}
          {joinable.length > 0 && (
            <>
              <p className="px-2 pb-1 pt-2 text-[10px] uppercase tracking-wide text-muted">Open spaces</p>
              {joinable.map((w) => (
                <button
                  key={w.id}
                  onClick={() => { joinWorkspace(w.id); setOpen(false); }}
                  className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-elevated"
                >
                  <span className="inline-flex size-6 items-center justify-center rounded-md bg-overlay text-[10px] font-bold text-ink-2">{w.short}</span>
                  <span className="min-w-0 flex-1 truncate text-sm text-ink-2">{w.name}</span>
                  <span className="inline-flex items-center gap-1 text-[11px] font-medium text-accent"><Globe className="size-3" />Join</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function UserMenu() {
  const { user, role, overridden, setRole } = useAuth();
  const { setWorkspace, workspaces, setTeams } = useWorkspace();
  const [open, setOpen] = React.useState(false);
  const ref = useDismiss(() => setOpen(false));
  const teams = user.teams;
  const toggleTeam = (t: string) => setTeams(teams.includes(t) ? teams.filter((x) => x !== t) : [...teams, t]);

  const signOut = () => {
    setRole(null);
    setTeams(null);
    if (workspaces[0]) setWorkspace(workspaces[0].id);
    setOpen(false);
    toast("Signed out (mock). Role reset to IAM default", "neutral");
  };

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} aria-label="Account menu" className="rounded-full outline-none ring-accent focus-visible:ring-2">
        <Avatar name={user.name} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-2 w-64 rounded-lg border border-border bg-surface p-1 shadow-lg">
          <div className="px-3 py-2.5">
            <p className="truncate text-sm font-medium text-ink">{user.name}</p>
            <p className="truncate text-xs text-muted">{user.email}</p>
            <span className={cn("mt-1.5 inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-medium", roleTone[role])}>
              {ROLE_LABEL[role]}{overridden && " · preview"}
            </span>
          </div>
          <div className="border-t border-border px-3 py-2">
            <p className="flex items-center gap-1.5 pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              <ShieldCheck className="size-3" /> Preview as role
            </p>
            <div className="grid grid-cols-2 gap-1">
              {ROLE_ORDER.map((r) => (
                <button
                  key={r}
                  onClick={() => setRole(r)}
                  className={cn(
                    "rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                    role === r ? "border-accent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2 hover:bg-overlay",
                  )}
                >
                  {ROLE_LABEL[r]}
                </button>
              ))}
            </div>
            <p className="pt-1.5 text-[11px] leading-snug text-muted">Dev-only override. Real role comes from {ORG.idpName}.</p>
          </div>
          <div className="border-t border-border px-3 py-2">
            <p className="pb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Preview teams</p>
            <div className="flex flex-wrap gap-1">
              {TEAMS.map((t) => (
                <button
                  key={t}
                  onClick={() => toggleTeam(t)}
                  className={cn(
                    "rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors",
                    teams.includes(t) ? "border-accent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2 hover:bg-overlay",
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
            <p className="pt-1.5 text-[11px] leading-snug text-muted">Drop a team to preview team-restricted access.</p>
          </div>
          <div className="border-t border-border p-1">
            <button
              onClick={signOut}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-ink-2 transition-colors hover:bg-elevated hover:text-ink"
            >
              <LogOut className="size-3.5" /> Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SidebarInner({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center px-5">
        <Brand />
      </div>
      <div className="px-3 pb-1">
        <WorkspaceSwitcher />
      </div>
      <NavLinks onNavigate={onNavigate} />
      <div className="border-t border-border p-3">
        <Link
          href="/settings"
          onClick={onNavigate}
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-2 transition-colors hover:bg-elevated hover:text-ink"
        >
          <Settings className="size-4" />
          Settings
        </Link>
      </div>
    </div>
  );
}

/** Thin context banner: read-only for viewers, preview notice when overriding. */
function ContextBanner() {
  const { role, overridden } = useAuth();
  const { workspace } = useWorkspace();
  if (role === "viewer") {
    return (
      <div className="border-b border-border bg-warning/10 px-4 py-1.5 text-center text-xs text-warning md:px-6">
        Read-only access in {workspace?.name ?? "this workspace"}. Your role is Viewer.
        {overridden && " (preview override)"}
      </div>
    );
  }
  if (overridden) {
    return (
      <div className="border-b border-border bg-accent-soft/60 px-4 py-1.5 text-center text-xs text-accent md:px-6">
        Previewing as {ROLE_LABEL[role]}. This overrides your {ORG.idpName} role.
      </div>
    );
  }
  return null;
}

/* @backend integration points (this page):
 *  - @backend workspace switcher - IAM memberships [via useWorkspace] (src/lib/iam.ts)
 *  - @backend user menu - OIDC session identity [via useAuth] (src/lib/auth.tsx)
 *  - @backend GET /v1/search - topbar global search box (non-functional today)
 *  - @backend NOTE the dev role switcher + team preview toggles are removed when real IAM lands
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const pathname = usePathname();
  return (
    <div className="min-h-[100dvh] bg-canvas">
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-border bg-surface lg:block">
        <SidebarInner />
      </aside>

      {/* mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-border bg-surface">
            <button onClick={() => setMobileOpen(false)} className="absolute right-3 top-3.5 text-muted hover:text-ink">
              <X className="size-5" />
            </button>
            <SidebarInner onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="lg:pl-60">
        {/* topbar */}
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-canvas/80 px-4 backdrop-blur md:px-6">
          <button onClick={() => setMobileOpen(true)} className="text-ink-2 lg:hidden" aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <div className="relative hidden max-w-md flex-1 md:block">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
            <Input placeholder="Search agents, tools, runs…" className="pl-9" />
          </div>
          <div className="ml-auto flex items-center gap-2">
            <GateButton permission="agent.create" href="/agents/new" variant="primary"><Plus className="size-4" /> New agent</GateButton>
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>

        <ContextBanner />

        <main key={pathname} className="reveal mx-auto max-w-[1400px] px-4 py-6 md:px-6 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
