"use client";

/* Admin-configurable per-resource access policy editor (Part B2). Set who may USE
   (invoke/bind) and who may MANAGE (edit/publish) a resource, by IAM role and team (Okta group).
   Shown read-only to non-admins. Enforcement lives in access-policy.ts + runtime-access.ts. */

import * as React from "react";
import { Lock, Globe, User } from "lucide-react";
import { Button, Separator } from "@/components/ui";
import { cn } from "@/lib/utils";
import { TEAMS } from "@/lib/iam";
import { ROLE_LABEL, ROLE_ORDER, type Role } from "@/lib/permissions";
import { type AccessPolicy, defaultPolicy, policySummary } from "@/lib/access-policy";
import { setAccessPolicy } from "@/lib/store";

type Kind = "agent" | "knowledge" | "tool" | "mcp" | "channel";

function Chips<T extends string>({
  options, selected, onToggle, disabled, label,
}: {
  options: readonly T[];
  selected: T[];
  onToggle: (v: T) => void;
  disabled?: boolean;
  label: (v: T) => string;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = selected.includes(o);
        return (
          <button
            key={o}
            type="button"
            disabled={disabled}
            onClick={() => onToggle(o)}
            className={cn(
              "rounded-md border px-2 py-0.5 text-[11px] font-medium leading-5 transition-colors disabled:opacity-60 disabled:pointer-events-none",
              on ? "border-transparent bg-accent-soft text-accent" : "border-border bg-elevated text-ink-2 hover:bg-overlay",
            )}
          >
            {label(o)}
          </button>
        );
      })}
    </div>
  );
}

function GrantRow({
  title, hint, roles, teams, onRoles, onTeams, disabled,
}: {
  title: string;
  hint: string;
  roles: Role[];
  teams: string[];
  onRoles: (r: Role) => void;
  onTeams: (t: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-2">
      <div>
        <p className="text-sm font-medium text-ink">{title}</p>
        <p className="text-xs text-ink-2">{hint}</p>
      </div>
      <div className="space-y-2">
        <div className="space-y-1">
          <p className="text-[11px] uppercase tracking-wide text-muted">Roles</p>
          <Chips options={ROLE_ORDER} selected={roles} onToggle={onRoles} disabled={disabled} label={(r) => ROLE_LABEL[r]} />
        </div>
        <div className="space-y-1">
          <p className="text-[11px] uppercase tracking-wide text-muted">Teams (Okta groups)</p>
          <Chips options={TEAMS} selected={teams} onToggle={onTeams} disabled={disabled} label={(t) => t} />
        </div>
      </div>
    </div>
  );
}

const toggle = <T,>(arr: T[], v: T): T[] => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

export function AccessPolicyEditor({
  kind, id, policy, readOnly = false,
}: {
  kind: Kind;
  id: string;
  policy: AccessPolicy | undefined;
  readOnly?: boolean;
}) {
  const [draft, setDraft] = React.useState<AccessPolicy>(policy ?? defaultPolicy());
  const restricted = draft.visibility === "restricted";
  const isPrivate = draft.visibility === "private";
  const dirty = JSON.stringify(draft) !== JSON.stringify(policy ?? defaultPolicy());

  const patch = (p: Partial<AccessPolicy>) => setDraft((d) => ({ ...d, ...p }));

  return (
    <div className="space-y-4">
      {/* visibility */}
      <div className="flex gap-2">
        {([
          { v: "workspace", icon: Globe, title: "All workspace members", sub: "Anyone in this workspace, per their role" },
          { v: "restricted", icon: Lock, title: "Restricted", sub: "Only the roles and teams you list below" },
          { v: "private", icon: User, title: "Private", sub: "Only the owner (and space admins)" },
        ] as const).map(({ v, icon: Icon, title, sub }) => {
          const on = draft.visibility === v;
          return (
            <button
              key={v}
              type="button"
              disabled={readOnly}
              onClick={() => patch({ visibility: v })}
              className={cn(
                "flex-1 rounded-lg border px-3 py-2.5 text-left transition-colors disabled:pointer-events-none",
                on ? "border-accent bg-accent-soft/40" : "border-border bg-elevated hover:bg-overlay",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium text-ink">
                <Icon className="size-3.5" aria-hidden />
                {title}
              </span>
              <span className="mt-0.5 block text-xs text-ink-2">{sub}</span>
            </button>
          );
        })}
      </div>

      {restricted && (
        <>
          <Separator />
          <GrantRow
            title="Can use"
            hint="Invoke this resource, or bind it into an agent."
            roles={draft.useRoles}
            teams={draft.useTeams}
            onRoles={(r) => patch({ useRoles: toggle(draft.useRoles, r) })}
            onTeams={(t) => patch({ useTeams: toggle(draft.useTeams, t) })}
            disabled={readOnly}
          />
          <GrantRow
            title="Can manage"
            hint="Edit configuration or publish this resource."
            roles={draft.manageRoles}
            teams={draft.manageTeams}
            onRoles={(r) => patch({ manageRoles: toggle(draft.manageRoles, r) })}
            onTeams={(t) => patch({ manageTeams: toggle(draft.manageTeams, t) })}
            disabled={readOnly}
          />
        </>
      )}

      {isPrivate && (
        <>
          <Separator />
          <p className="text-xs text-ink-2">
            Only the owner{draft.owner ? <> (<span className="text-ink">{draft.owner}</span>)</> : " (set to you on save)"} and
            space admins can use or manage this resource. Run-time callers (tokens/channels) are always denied.
          </p>
        </>
      )}

      <div className="flex items-center justify-between gap-3 pt-1">
        <p className="text-xs text-ink-2">
          Effective: <span className="text-ink">{policySummary(draft)}</span>
        </p>
        {readOnly ? (
          <p className="text-xs text-muted">Managed by an admin</p>
        ) : (
          <div className="flex gap-2">
            {dirty && (
              <Button variant="ghost" size="sm" onClick={() => setDraft(policy ?? defaultPolicy())}>
                Reset
              </Button>
            )}
            <Button size="sm" variant="primary" disabled={!dirty} onClick={() => setAccessPolicy(kind, id, draft)}>
              Save policy
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
