/* ============================================================
   Credential masking helpers — PURE (no store/data imports), safe to use anywhere.

   @backend These are a frontend MOCK stand-in for the vault. In production the raw secret is POSTed
   to the vault (Transit envelope) and the server returns { masked, vaultPath }; the client never
   derives either value and never stores the raw secret. Mirrors the provider_keys.masked/vault_path
   pattern. [ref: backend-integration-plan.md Part 1 step 11, backend/01-data-model.md §4.6]
   ============================================================ */

/** Mask a text secret, keeping a short head/tail for recognizability. */
export function maskSecret(secret: string): string {
  const s = secret.trim();
  if (!s) return "";
  if (s.length <= 10) return "•".repeat(s.length);
  return s.slice(0, 6) + "••••••••" + s.slice(-4);
}

/** Mask a file-based credential (e.g. a service-account JSON key) from its name — the contents are
    never read on the client. */
export function maskFileName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "";
  const dot = trimmed.lastIndexOf(".");
  const ext = dot > 0 ? trimmed.slice(dot) : "";
  const base = dot > 0 ? trimmed.slice(0, dot) : trimmed;
  return `${base.slice(0, 4)}••••••••${ext}`;
}

/** Placeholder vault reference. The real vault path is returned by the server on credential submit. */
export function vaultRef(kind: string, id: string): string {
  return `vault://${kind}/${id}`;
}
