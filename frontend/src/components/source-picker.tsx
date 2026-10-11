"use client";

import * as React from "react";
import { Check, ShieldCheck } from "lucide-react";
import { FileDropzone } from "./file-dropzone";
import { Button, Field, Input, Select, Textarea } from "./ui";
import { genId, nowIso } from "@/lib/store";
import { maskFileName, maskSecret, vaultRef } from "@/lib/secrets";
import {
  CONNECTOR_DEFS, CONNECTORS, secretField, type Connector, type ConnectorDef,
} from "@/lib/connectors";
import type { CredentialRef, DataSource, DataSourceConfig } from "@/lib/data";

// @backend GET /v1/connectors - the available source kinds + the connector registry (field specs per
//   connector) should come from what the backend actually supports. File upload needs a presigned-URL/
//   upload endpoint. Credential capture: the raw secret/file is POSTed once to the vault, which returns
//   { masked, vaultPath } - the client-side masking below is a MOCK stand-in and never persists raw
//   secrets. OAuth is a simulated connect here; real impl is a server-side redirect/callback.
//   [ref: backend-integration-plan.md Part 2, backend/08-api-surface.md]
export type SourceKind = "File upload" | "Web crawl" | "Connector" | "Manual" | "Skip for now";
export type { Connector };

/** Ephemeral credential input for one connector. Raw values never leave the browser except to the
    vault; only a masked CredentialRef is persisted on the DataSource. */
export type ConnectorCredential = {
  values?: Record<string, string>; // credentialField values (e.g. access key id, secret, token)
  file?: File; // service-account key file
  fileName?: string;
  connected?: boolean; // oauth2 simulated connect-state
};

export type SourceState = {
  kind: SourceKind;
  files: File[];
  seedUrl: string;
  depth: string;
  connector: Connector;
  // per-connector maps so switching connectors never bleeds or collides values
  connectorFields: Partial<Record<Connector, Record<string, string>>>;
  credentials: Partial<Record<Connector, ConnectorCredential>>;
  manual: string;
};

export const initialSourceState: SourceState = {
  kind: "File upload",
  files: [],
  seedUrl: "",
  depth: "2",
  connector: "S3",
  connectorFields: {},
  credentials: {},
  manual: "",
};

/* ---------------------------------------------------------------- pure helpers */

const fieldsOf = (s: SourceState): Record<string, string> => s.connectorFields[s.connector] ?? {};
const credOf = (s: SourceState): ConnectorCredential => s.credentials[s.connector] ?? {};

function configComplete(def: ConnectorDef, fields: Record<string, string>): boolean {
  return def.configFields.every((f) => !f.required || (fields[f.key] ?? "").trim().length > 0);
}

function credentialComplete(def: ConnectorDef, cred: ConnectorCredential): boolean {
  switch (def.auth) {
    case "oauth2": return !!cred.connected;
    case "service_account": return !!cred.file;
    default: return def.credentialFields.every((f) => !f.required || (cred.values?.[f.key] ?? "").trim().length > 0);
  }
}

/** "a source is chosen" (vs. skipped). */
export function hasConfiguredSource(s: SourceState): boolean {
  return s.kind !== "Skip for now";
}

/** "the chosen source is valid and ready to submit" — gates Next / Create / Add source. */
export function isSourceComplete(s: SourceState): boolean {
  switch (s.kind) {
    case "Skip for now": return true;
    case "File upload": return s.files.length > 0;
    case "Web crawl": return s.seedUrl.trim().length > 0;
    case "Manual": return s.manual.trim().length > 0;
    case "Connector": {
      const def = CONNECTOR_DEFS[s.connector];
      return configComplete(def, fieldsOf(s)) && credentialComplete(def, credOf(s));
    }
  }
}

const compact = (fields: Record<string, string>): Record<string, string> =>
  Object.fromEntries(Object.entries(fields).filter(([, v]) => v && v.trim().length > 0));

function connectorSourceName(connector: Connector, fields: Record<string, string>): string {
  const primary = CONNECTOR_DEFS[connector].configFields.map((f) => fields[f.key]).find((v) => v && v.trim());
  return primary ? `${connector}: ${primary}` : connector;
}

/** Map the picker state to a DataSource (config only, NO credential), or null when skipping.
    Pure — the masked credential is attached separately at submit via finalizeCredential. */
export function buildDataSource(s: SourceState): DataSource | null {
  if (s.kind === "Skip for now") return null;
  const base = { status: "syncing" as const, docs: 0, updatedAt: nowIso() };

  switch (s.kind) {
    case "File upload": {
      const name =
        s.files.length === 0 ? "File upload"
          : s.files.length === 1 ? s.files[0].name
            : `${s.files[0].name} (+${s.files.length - 1} more)`;
      return { name, type: "File", ...base };
    }
    case "Web crawl":
      return { name: s.seedUrl.trim() || "Web crawl", type: "Web", ...base };
    case "Connector": {
      const fields = fieldsOf(s);
      return {
        name: connectorSourceName(s.connector, fields), type: "Connector", ...base,
        config: { connector: s.connector, fields: compact(fields) },
      };
    }
    case "Manual":
      return { name: "Manual entry", type: "Manual", ...base };
  }
}

/** Derive the masked credential reference to persist. Raw secret/file content never leaves here. */
export function finalizeCredential(s: SourceState): CredentialRef | undefined {
  if (s.kind !== "Connector") return undefined;
  const def = CONNECTOR_DEFS[s.connector];
  const cred = credOf(s);
  const path = vaultRef("connector", genId("src"));

  switch (def.auth) {
    case "oauth2":
      return cred.connected ? { masked: "oauth:••••••••", vaultPath: path } : undefined;
    case "service_account":
      return cred.file ? { masked: maskFileName(cred.fileName ?? cred.file.name), vaultPath: path } : undefined;
    default: {
      const sf = secretField(def);
      const raw = sf ? (cred.values?.[sf.key] ?? "") : "";
      return raw.trim() ? { masked: maskSecret(raw), vaultPath: path } : undefined;
    }
  }
}

/** One-line summary of a picker state (used in the new-KB review step). */
export function summarizeSource(s: SourceState): string {
  switch (s.kind) {
    case "File upload": return s.files.length ? `File upload (${s.files.length})` : "File upload";
    case "Web crawl": return `Web crawl${s.seedUrl.trim() ? `: ${s.seedUrl.trim()}` : ""}`;
    case "Connector": return connectorSourceName(s.connector, fieldsOf(s));
    case "Manual": return "Manual entry";
    default: return "Skipped";
  }
}

/** One-line summary of a PERSISTED datasource's connector config (used on the KB-detail source list). */
export function describeDataSourceConfig(config?: DataSourceConfig): string | null {
  if (!config?.connector) return null;
  const def = CONNECTOR_DEFS[config.connector];
  if (!def) return config.connector; // unknown/legacy connector — show the raw name rather than crash
  const parts: string[] = [];
  const fields = config.fields ?? {};
  for (const f of def.configFields) {
    const v = fields[f.key];
    if (v && v.trim()) parts.push(`${f.label} ${v}`);
  }
  if (config.credentialRef) parts.push(config.credentialRef.masked);
  return parts.length ? parts.join(" · ") : config.connector;
}

/* ---------------------------------------------------------------- component */

export function SourcePicker({
  value, onChange, allowSkip = false,
}: {
  value: SourceState;
  onChange: (next: SourceState) => void;
  allowSkip?: boolean;
}) {
  const [tested, setTested] = React.useState(false);
  const set = (patch: Partial<SourceState>) => onChange({ ...value, ...patch });

  const setField = (key: string, v: string) => {
    setTested(false);
    set({ connectorFields: { ...value.connectorFields, [value.connector]: { ...(value.connectorFields[value.connector] ?? {}), [key]: v } } });
  };
  const setCred = (patch: Partial<ConnectorCredential>) => {
    setTested(false);
    set({ credentials: { ...value.credentials, [value.connector]: { ...(value.credentials[value.connector] ?? {}), ...patch } } });
  };
  const setCredValue = (key: string, v: string) =>
    setCred({ values: { ...(value.credentials[value.connector]?.values ?? {}), [key]: v } });

  const def = CONNECTOR_DEFS[value.connector];
  const fields = fieldsOf(value);
  const cred = credOf(value);

  return (
    <div className="space-y-4">
      <Field label="Source type">
        <Select value={value.kind} onChange={(e) => { setTested(false); set({ kind: e.target.value as SourceKind }); }}>
          <option value="File upload">File upload</option>
          <option value="Web crawl">Web crawl</option>
          <option value="Connector">Connector</option>
          <option value="Manual">Manual</option>
          {allowSkip && <option value="Skip for now">Skip for now</option>}
        </Select>
      </Field>

      {value.kind === "File upload" && (
        <FileDropzone onChange={(files) => set({ files })} />
      )}

      {value.kind === "Web crawl" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Seed URL" hint="Crawl starts from this page">
            <Input placeholder="https://docs.example.com" value={value.seedUrl} onChange={(e) => set({ seedUrl: e.target.value })} />
          </Field>
          <Field label="Crawl depth" hint="Link levels to follow">
            <Input type="number" min={0} value={value.depth} onChange={(e) => set({ depth: e.target.value })} />
          </Field>
        </div>
      )}

      {value.kind === "Connector" && (
        <div className="space-y-4">
          <Field label="Connector">
            <Select value={value.connector} onChange={(e) => { setTested(false); set({ connector: e.target.value as Connector }); }}>
              {CONNECTORS.map((c) => <option key={c} value={c}>{CONNECTOR_DEFS[c].label}</option>)}
            </Select>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            {def.configFields.map((f) => (
              <Field key={f.key} label={f.required ? `${f.label} *` : f.label} hint={f.hint}>
                <Input
                  type={f.type === "number" ? "number" : "text"}
                  placeholder={f.placeholder}
                  value={fields[f.key] ?? ""}
                  onChange={(e) => setField(f.key, e.target.value)}
                />
              </Field>
            ))}
          </div>

          <div className="space-y-3 rounded-lg border border-border bg-elevated p-3">
            <div className="text-xs font-medium text-ink-2">Credentials</div>

            {(def.auth === "access_key" || def.auth === "token") && (
              <div className="grid gap-4 sm:grid-cols-2">
                {def.credentialFields.map((f) => (
                  <Field key={f.key} label={f.required ? `${f.label} *` : f.label} hint={f.hint}>
                    <Input
                      type={f.type === "password" ? "password" : "text"}
                      placeholder={f.placeholder}
                      value={cred.values?.[f.key] ?? ""}
                      onChange={(e) => setCredValue(f.key, e.target.value)}
                    />
                  </Field>
                ))}
              </div>
            )}

            {def.auth === "oauth2" && (
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant={cred.connected ? "outline" : "primary"}
                  size="sm"
                  disabled={!configComplete(def, fields)}
                  onClick={() => setCred({ connected: !cred.connected })}
                >
                  {cred.connected ? <><Check className="size-3.5" /> Connected</> : <>Connect {def.label}</>}
                </Button>
                {cred.connected && <span className="text-xs text-good">Authorized (simulated)</span>}
                {!configComplete(def, fields) && <span className="text-xs text-muted">Fill the fields above to connect.</span>}
              </div>
            )}

            {def.auth === "service_account" && (
              <Field label="Service-account JSON key *">
                <Input
                  type="file"
                  accept="application/json,.json"
                  onChange={(e) => { const file = e.target.files?.[0]; setCred({ file: file ?? undefined, fileName: file?.name }); }}
                />
                {cred.fileName && <p className="mt-1 text-xs text-ink-2">{cred.fileName}</p>}
              </Field>
            )}

            {def.credentialHint && <p className="text-xs text-muted">{def.credentialHint}</p>}
          </div>

          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" disabled={!isSourceComplete(value)} onClick={() => setTested(true)}>
              <ShieldCheck className="size-3.5" /> Test connection
            </Button>
            {tested && <span className="text-xs text-good">Connection ok (simulated)</span>}
          </div>
        </div>
      )}

      {value.kind === "Manual" && (
        <Field label="Content" hint="Paste text to index directly">
          <Textarea
            rows={6}
            placeholder="Paste documents, notes or FAQ content here..."
            value={value.manual}
            onChange={(e) => set({ manual: e.target.value })}
          />
        </Field>
      )}

      {value.kind === "Skip for now" && (
        <p className="rounded-lg border border-dashed border-border bg-elevated px-3 py-4 text-xs text-muted">
          You can add data sources later from the knowledge base page.
        </p>
      )}
    </div>
  );
}
