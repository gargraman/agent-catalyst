import Link from "next/link";
import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

/* ----------------------------------------------------------------- Card */
export function Card({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("card-elev rounded-xl border border-border bg-surface", className)} {...p} />;
}
export function CardHeader({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex items-start justify-between gap-4 px-5 pt-5", className)} {...p} />;
}
export function CardTitle({ className, ...p }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-sm font-semibold text-ink", className)} {...p} />;
}
export function CardDescription({ className, ...p }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-0.5 text-xs text-muted", className)} {...p} />;
}
export function CardContent({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...p} />;
}

/* --------------------------------------------------------------- Button */
type BtnVariant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type BtnSize = "sm" | "md" | "lg" | "icon";
const btnBase =
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150 active:translate-y-px disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap";
const btnVariants: Record<BtnVariant, string> = {
  primary: "bg-accent text-accent-ink hover:bg-accent-hover shadow-sm shadow-accent/20",
  secondary: "bg-elevated text-ink border border-border hover:bg-overlay",
  outline: "border border-border text-ink hover:bg-elevated",
  ghost: "text-ink-2 hover:bg-elevated hover:text-ink",
  danger: "bg-critical text-white hover:opacity-90",
};
const btnSizes: Record<BtnSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-10 px-5 text-sm",
  icon: "h-9 w-9",
};
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant;
  size?: BtnSize;
  href?: string;
};
export function Button({ className, variant = "secondary", size = "md", href, ...p }: ButtonProps) {
  const cls = cn(btnBase, btnVariants[variant], btnSizes[size], className);
  if (href) return <Link href={href} className={cls}>{p.children}</Link>;
  return <button className={cls} {...p} />;
}

/* ---------------------------------------------------------------- Badge */
type Tone = "neutral" | "accent" | "good" | "warning" | "serious" | "critical" | "violet";
const toneMap: Record<Tone, string> = {
  neutral: "bg-elevated text-ink-2 border-border",
  accent: "bg-accent-soft text-accent border-transparent",
  good: "bg-good/12 text-good border-transparent",
  warning: "bg-warning/15 text-warning border-transparent",
  serious: "bg-serious/15 text-serious border-transparent",
  critical: "bg-critical/12 text-critical border-transparent",
  violet: "bg-s7/15 text-s7 border-transparent",
};
export function Badge({ tone = "neutral", className, ...p }: { tone?: Tone } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium leading-5",
        toneMap[tone],
        className,
      )}
      {...p}
    />
  );
}

export function StatusDot({ tone = "neutral", className }: { tone?: Tone; className?: string }) {
  const bg: Record<Tone, string> = {
    neutral: "bg-muted", accent: "bg-accent", good: "bg-good", warning: "bg-warning",
    serious: "bg-serious", critical: "bg-critical", violet: "bg-s7",
  };
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", bg[tone], className)} />;
}

/* ---------------------------------------------------------------- Inputs */
export function Input({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-lg border border-border bg-elevated px-3 text-sm text-ink placeholder:text-muted",
        "focus:border-accent focus:outline-none",
        className,
      )}
      {...p}
    />
  );
}
export function Textarea({ className, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-lg border border-border bg-elevated px-3 py-2 text-sm text-ink placeholder:text-muted",
        "focus:border-accent focus:outline-none resize-y",
        className,
      )}
      {...p}
    />
  );
}
export function Select({ className, children, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 w-full appearance-none rounded-lg border border-border bg-elevated px-3 pr-8 text-sm text-ink",
        "focus:border-accent focus:outline-none",
        "bg-[length:14px] bg-[right_0.6rem_center] bg-no-repeat",
        className,
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2383838f' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
      }}
      {...p}
    >
      {children}
    </select>
  );
}
export function Label({ className, ...p }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("text-xs font-medium text-ink-2", className)} {...p} />;
}

export function Field({ label, hint, children }: { label: React.ReactNode; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- Table */
export function Table({ className, ...p }: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...p} />
    </div>
  );
}
export function THead({ className, ...p }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn("", className)} {...p} />;
}
export function TH({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-border px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted",
        className,
      )}
      {...p}
    />
  );
}
export function TR({ className, ...p }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("border-b border-border/70 last:border-0 hover:bg-elevated/60 transition-colors", className)} {...p} />;
}
export function TD({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-4 py-3 align-middle text-ink-2", className)} {...p} />;
}

/* --------------------------------------------------------- Misc helpers */
export function Separator({ className }: { className?: string }) {
  return <div className={cn("h-px w-full bg-border", className)} />;
}

export function Avatar({ name, className }: { name: string; className?: string }) {
  const initials = name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <span className={cn("inline-flex size-7 items-center justify-center rounded-full bg-elevated text-[11px] font-semibold text-ink-2 border border-border", className)}>
      {initials}
    </span>
  );
}

export function Progress({ value, tone = "accent", className }: { value: number; tone?: Tone; className?: string }) {
  const bar: Record<Tone, string> = {
    neutral: "bg-muted", accent: "bg-accent", good: "bg-good", warning: "bg-warning",
    serious: "bg-serious", critical: "bg-critical", violet: "bg-s7",
  };
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-elevated", className)}>
      <div className={cn("h-full rounded-full transition-all", bar[tone])} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

export function EmptyState({ icon, title, hint }: { icon?: React.ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border py-14 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-sm text-xs text-muted">{hint}</p>}
    </div>
  );
}

export function PageHeader({
  title, description, children,
}: { title: string; description?: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- Pagination */
/** Reusable footer pagination: result range, page-size picker, prev/next.
    Page is 1-indexed. Render only when there is at least one result. */
export function Pagination({
  page, pageSize, total, onPageChange, onPageSizeChange,
  pageSizeOptions = [10, 20, 50], className,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3", className)}>
      <p className="text-xs text-muted">
        {total === 0 ? "No results" : (
          <>Showing <span className="tnum text-ink-2">{start}</span>-<span className="tnum text-ink-2">{end}</span> of <span className="tnum text-ink-2">{total}</span></>
        )}
      </p>
      <div className="flex items-center gap-3">
        {onPageSizeChange && (
          <label className="flex items-center gap-2 text-xs text-muted">
            Per page
            <div className="w-20">
              <Select value={String(pageSize)} onChange={(e) => onPageSizeChange(Number(e.target.value))} className="h-8">
                {pageSizeOptions.map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </div>
          </label>
        )}
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon" className="size-8" aria-label="Previous page" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
            <ChevronLeft className="size-4" />
          </Button>
          <span className="px-2 text-xs tnum text-ink-2">Page {page} of {pageCount}</span>
          <Button variant="outline" size="icon" className="size-8" aria-label="Next page" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Mono code block with copy affordance styling. */
export function CodeBlock({ code, className }: { code: string; className?: string }) {
  return (
    <pre className={cn("overflow-x-auto rounded-lg border border-border bg-canvas p-4 font-mono text-xs leading-relaxed text-ink-2", className)}>
      <code>{code}</code>
    </pre>
  );
}
