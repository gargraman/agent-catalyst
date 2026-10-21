/* Shared UI for the shared-service vs per-user credential mode (doc 17), used by both the MCP new-server
   wizard and the HTTP-tool dialog so the toggle, OAuth-app fields, and connect note stay in one place. */

import * as React from "react";
import { Info } from "lucide-react";
import { Field, Input } from "@/components/ui";
import { PER_USER_OAUTH_APP_FIELDS, PER_USER_OAUTH_SCOPES_FIELD } from "@/lib/oauth-app";

export type AuthModeValue = "shared_service" | "per_user";

/** Two-card selector: one fixed shared credential vs each caller connecting their own account. */
export function CredentialModeToggle({
  value, onChange, hint = "Who provides the credential this calls with", perUserSub = "Each user connects their own account (GitHub, Jira, …)",
}: {
  value: AuthModeValue;
  onChange: (v: AuthModeValue) => void;
  hint?: string;
  perUserSub?: string;
}) {
  const opts = [
    { v: "shared_service" as const, title: "Shared service credential", sub: "One fixed credential used for every caller" },
    { v: "per_user" as const, title: "Per-user credentials", sub: perUserSub },
  ];
  return (
    <Field label="Credential mode" hint={hint}>
      <div className="flex gap-2">
        {opts.map(({ v, title, sub }) => {
          const on = value === v;
          return (
            <button
              key={v}
              type="button"
              onClick={() => onChange(v)}
              className={`flex-1 rounded-lg border px-3 py-2.5 text-left transition-colors ${on ? "border-accent bg-accent-soft/40" : "border-border bg-elevated hover:bg-overlay"}`}
            >
              <span className="block text-sm font-medium text-ink">{title}</span>
              <span className="mt-0.5 block text-xs text-ink-2">{sub}</span>
            </button>
          );
        })}
      </div>
    </Field>
  );
}

/** The OAuth-app credential inputs (client id/secret + scopes) + the per-user connect note.
 *  `keepExisting` shows masked placeholders when editing a resource whose app creds are already vaulted. */
export function PerUserOAuthFields({
  values, onChange, keepExisting = false,
}: {
  values: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  keepExisting?: boolean;
}) {
  const fields = [...PER_USER_OAUTH_APP_FIELDS, PER_USER_OAUTH_SCOPES_FIELD];
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map((f) => (
          <Field key={f.key} label={f.required ? `${f.label} *` : f.label} hint={f.hint}>
            <Input
              type={f.type === "password" ? "password" : "text"}
              value={values[f.key] ?? ""}
              onChange={(e) => onChange({ ...values, [f.key]: e.target.value })}
              placeholder={keepExisting && f.type === "password" ? "•••••••• (unchanged)" : f.placeholder}
            />
          </Field>
        ))}
      </div>
      <div className="flex gap-2.5 rounded-lg border border-border bg-elevated/60 px-3 py-2.5 text-xs leading-relaxed text-ink-2">
        <Info className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden />
        <p className="max-w-[72ch]">
          Each user connects their <strong>own</strong> account via OAuth on first use; the run fails closed
          until they connect. These are the shared OAuth <strong>app</strong> credentials — individual user
          tokens are never stored here.
        </p>
      </div>
    </>
  );
}
