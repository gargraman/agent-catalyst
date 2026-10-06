/* ============================================================
   Connector registry — declarative definition of each knowledge-base datasource connector:
   its non-secret config fields and its credential/auth shape. The UI renders from this; adding a
   connector is a data change, not new form code.

   @backend GET /v1/connectors should return this registry (field specs per connector) so the UI is
   driven by what the backend actually supports. Connector ingestion itself is P2 on the backend;
   this frontend config + credential-capture lands ahead of it. [ref: backend/04-rag.md §3/§10]
   ============================================================ */

export type Connector = "S3" | "Confluence" | "Git" | "Google Drive";

/** How a connector's credential is captured — the single discriminant the picker switches on:
 *   - access_key / token : render `credentialFields` as inputs (the password field is the secret)
 *   - oauth2             : no fields; a "Connect" button establishes connect-state + a synthesized token
 *   - service_account    : a credential FILE (e.g. a JSON key), masked from its filename */
export type AuthMethod = "oauth2" | "access_key" | "token" | "service_account";

export type FieldSpec = {
  key: string;
  label: string;
  type: "text" | "password" | "number";
  hint?: string;
  placeholder?: string;
  required?: boolean;
};

export type ConnectorDef = {
  id: Connector;
  label: string;
  auth: AuthMethod;
  configFields: FieldSpec[];
  /** Secret fields for access_key/token; empty for oauth2 & service_account (handled specially). */
  credentialFields: FieldSpec[];
  /** Short note rendered under the credential UI (e.g. the simulated-OAuth / file notice). */
  credentialHint?: string;
};

export const CONNECTOR_DEFS: Record<Connector, ConnectorDef> = {
  S3: {
    id: "S3",
    label: "Amazon S3",
    auth: "access_key",
    configFields: [
      { key: "bucket", label: "Bucket", type: "text", placeholder: "intel-reports", required: true },
      { key: "prefix", label: "Prefix", type: "text", placeholder: "reports/2026/", hint: "Optional key prefix to scope the sync" },
      { key: "region", label: "Region", type: "text", placeholder: "us-east-1" },
    ],
    credentialFields: [
      { key: "accessKeyId", label: "Access key ID", type: "text", placeholder: "AKIA…", required: true },
      { key: "secretAccessKey", label: "Secret access key", type: "password", placeholder: "Paste secret", required: true },
    ],
    credentialHint: "Stored in the vault. Only a masked reference is kept.",
  },
  Confluence: {
    id: "Confluence",
    label: "Confluence",
    auth: "oauth2",
    configFields: [
      { key: "baseUrl", label: "Base URL", type: "text", placeholder: "https://org.atlassian.net/wiki", required: true },
      { key: "spaceKey", label: "Space key", type: "text", placeholder: "ENG", required: true },
    ],
    credentialFields: [],
    credentialHint: "Simulated OAuth — no real redirect is performed in this build.",
  },
  Git: {
    id: "Git",
    label: "Git / GitHub",
    auth: "token",
    configFields: [
      { key: "repoUrl", label: "Repository URL", type: "text", placeholder: "https://github.com/org/repo", required: true },
      { key: "branch", label: "Branch", type: "text", placeholder: "main" },
    ],
    credentialFields: [
      { key: "token", label: "Personal access token", type: "password", placeholder: "ghp_…", required: true },
    ],
    credentialHint: "Stored in the vault. Only a masked reference is kept.",
  },
  "Google Drive": {
    id: "Google Drive",
    label: "Google Drive",
    auth: "service_account",
    configFields: [
      { key: "folderId", label: "Folder ID", type: "text", placeholder: "1A2b3C…", required: true },
    ],
    credentialFields: [],
    credentialHint: "Upload a service-account JSON key. The file is sent to the vault; only its masked name is kept.",
  },
};

/** Ordered list for the connector <Select>. */
export const CONNECTORS: Connector[] = ["S3", "Confluence", "Git", "Google Drive"];

/** The designated secret field for text-credential connectors (the one that gets masked), if any. */
export function secretField(def: ConnectorDef): FieldSpec | undefined {
  return def.credentialFields.find((f) => f.type === "password");
}
