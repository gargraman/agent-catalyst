import * as React from "react";
import { cn } from "@/lib/utils";

const SERIES = ["var(--color-s1)", "var(--color-s2)", "var(--color-s3)", "var(--color-s4)", "var(--color-s5)", "var(--color-s6)", "var(--color-s7)", "var(--color-s8)"];

type Series = { name: string; values: number[] };

function niceMax(v: number) {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

/* ------------------------------------------------------- Area / line chart */
export function AreaChart({
  series, labels, height = 220, area = true, format = (n: number) => String(n), className,
}: {
  series: Series[]; labels: string[]; height?: number; area?: boolean;
  format?: (n: number) => string; className?: string;
}) {
  const W = 820, H = height, padL = 46, padR = 14, padT = 14, padB = 26;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values)));
  const n = labels.length;
  const x = (i: number) => padL + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (v: number) => padT + ih - (v / max) * ih;
  const ticks = 4;

  return (
    <div className={className}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="auto" role="img" preserveAspectRatio="xMidYMid meet">
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const gy = padT + (i / ticks) * ih;
          const val = max - (i / ticks) * max;
          return (
            <g key={i}>
              <line x1={padL} y1={gy} x2={W - padR} y2={gy} stroke="var(--color-border)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <text x={padL - 8} y={gy + 3} textAnchor="end" fontSize={10} fill="var(--color-muted)" className="tnum">{format(val)}</text>
            </g>
          );
        })}
        {labels.map((l, i) => (
          (n <= 8 || i % Math.ceil(n / 8) === 0) && (
            <text key={i} x={x(i)} y={H - 8} textAnchor="middle" fontSize={10} fill="var(--color-muted)">{l}</text>
          )
        ))}
        {series.map((s, si) => {
          const color = SERIES[si % SERIES.length];
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          const areaPts = `${padL},${y(0)} ${pts} ${x(n - 1)},${y(0)}`;
          return (
            <g key={si}>
              {area && si < 2 && <polyline points={areaPts} fill={color} opacity={0.1} stroke="none" />}
              <polyline points={pts} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={8} fill="transparent">
                  <title>{`${s.name} · ${labels[i]}: ${format(v)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      {series.length > 1 && <Legend items={series.map((s) => s.name)} />}
    </div>
  );
}

/* --------------------------------------------------------------- Bar chart */
export function BarChart({
  data, height = 220, format = (n: number) => String(n), tone, className,
}: {
  data: { label: string; value: number }[]; height?: number;
  format?: (n: number) => string; tone?: string; className?: string;
}) {
  const W = 820, H = height, padL = 46, padR = 14, padT = 14, padB = 28;
  const iw = W - padL - padR, ih = H - padT - padB;
  const max = niceMax(Math.max(1, ...data.map((d) => d.value)));
  const n = data.length;
  const bw = Math.min(42, (iw / n) * 0.6);
  const gap = iw / n;
  const y = (v: number) => padT + ih - (v / max) * ih;
  const ticks = 4;
  return (
    <div className={className}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="auto" role="img" preserveAspectRatio="xMidYMid meet">
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const gy = padT + (i / ticks) * ih;
          return (
            <g key={i}>
              <line x1={padL} y1={gy} x2={W - padR} y2={gy} stroke="var(--color-border)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <text x={padL - 8} y={gy + 3} textAnchor="end" fontSize={10} fill="var(--color-muted)" className="tnum">{format(max - (i / ticks) * max)}</text>
            </g>
          );
        })}
        {data.map((d, i) => {
          const cx = padL + gap * i + gap / 2;
          const h = ih - (y(d.value) - padT);
          return (
            <g key={i}>
              <rect x={cx - bw / 2} y={y(d.value)} width={bw} height={Math.max(2, h)} rx={4} fill={tone ?? SERIES[0]}>
                <title>{`${d.label}: ${format(d.value)}`}</title>
              </rect>
              <text x={cx} y={H - 9} textAnchor="middle" fontSize={10} fill="var(--color-muted)">{d.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------- Donut */
export function Donut({
  data, size = 160, thickness = 20, className,
}: {
  data: { label: string; value: number; color?: string }[]; size?: number; thickness?: number; className?: string;
}) {
  const total = data.reduce((a, b) => a + b.value, 0) || 1;
  const r = (size - thickness) / 2;
  const c = size / 2;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className={cn("flex items-center gap-5", className)}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
        <circle cx={c} cy={c} r={r} fill="none" stroke="var(--color-elevated)" strokeWidth={thickness} />
        {data.map((d, i) => {
          const frac = d.value / total;
          const len = frac * circ;
          const el = (
            <circle
              key={i} cx={c} cy={c} r={r} fill="none"
              stroke={d.color ?? SERIES[i % SERIES.length]} strokeWidth={thickness}
              strokeDasharray={`${len} ${circ - len}`} strokeDashoffset={-offset}
              transform={`rotate(-90 ${c} ${c})`} strokeLinecap="butt"
            >
              <title>{`${d.label}: ${d.value} (${((frac) * 100).toFixed(0)}%)`}</title>
            </circle>
          );
          offset += len;
          return el;
        })}
        <text x={c} y={c - 2} textAnchor="middle" fontSize={22} fontWeight={600} fill="var(--color-ink)" className="tnum">{total}</text>
        <text x={c} y={c + 16} textAnchor="middle" fontSize={10} fill="var(--color-muted)">total</text>
      </svg>
      <ul className="space-y-1.5 text-sm">
        {data.map((d, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className="size-2.5 rounded-sm" style={{ background: d.color ?? SERIES[i % SERIES.length] }} />
            <span className="text-ink-2">{d.label}</span>
            <span className="ml-auto pl-4 tnum text-muted">{d.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* --------------------------------------------------------------- Sparkline */
export function Sparkline({ values, tone, width = 120, height = 34 }: { values: number[]; tone?: string; width?: number; height?: number }) {
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const n = values.length;
  const x = (i: number) => (n <= 1 ? width / 2 : (i / (n - 1)) * width);
  const y = (v: number) => height - ((v - min) / span) * (height - 4) - 2;
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <polyline points={`0,${height} ${pts} ${width},${height}`} fill={tone ?? "var(--color-s1)"} opacity={0.12} stroke="none" />
      <polyline points={pts} fill="none" stroke={tone ?? "var(--color-s1)"} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Legend({ items }: { items: string[] }) {
  return (
    <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((it, i) => (
        <li key={it} className="flex items-center gap-1.5 text-xs text-ink-2">
          <span className="h-2 w-3 rounded-sm" style={{ background: SERIES[i % SERIES.length] }} />
          {it}
        </li>
      ))}
    </ul>
  );
}
