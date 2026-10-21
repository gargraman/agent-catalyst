"use client";

import Link from "next/link";
import { Sparkles, Plus, ShieldCheck, ShieldAlert, Users, FileText, BadgeCheck } from "lucide-react";
import { Badge, StatusDot, PageHeader } from "@/components/ui";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { useSkills } from "@/lib/store";
import { usePrincipal } from "@/lib/auth";
import { canUse } from "@/lib/access-policy";
import { relative } from "@/lib/format";
import type { Skill } from "@/lib/data";

/** One-line disambiguation so Skills don't become a dumping ground (shared with the new-skill page). */
export const SKILL_BLURB =
  "A skill is a reusable instruction playbook + reference files shared across agents. It doesn't call APIs — that's a Tool. It isn't searchable documents — that's a Knowledge Base.";

const statusTone = (s: Skill["status"]) => (s === "published" ? "good" : s === "deprecated" ? "neutral" : "warning");

/* @backend integration points (this page):
 *  - @backend GET /v1/skills?workspace=:id - list skills (instruction+resource capability packages) [via useSkills]
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function SkillsPage() {
  const principal = usePrincipal();
  const skills = useSkills().filter((s) => canUse(s.accessPolicy, principal));

  const published = skills.filter((s) => s.status === "published").length;
  const drafts = skills.filter((s) => s.status === "draft").length;
  const signed = skills.filter((s) => s.signature).length;

  return (
    <div className="space-y-6">
      <PageHeader title="Skills" description="Reusable instruction playbooks you can attach to agents — like Knowledge and Tools, but for how an agent works.">
        <GateButton permission="skill.manage" variant="primary" href="/skills/new"><Plus className="size-4" /> New skill</GateButton>
      </PageHeader>

      <p className="-mt-2 max-w-3xl text-sm text-muted">{SKILL_BLURB}</p>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Skills" value={String(skills.length)} icon={<Sparkles className="size-4" />} />
        <StatTile label="Published" value={String(published)} icon={<BadgeCheck className="size-4" />} />
        <StatTile label="Drafts" value={String(drafts)} hint="not yet bindable" icon={<FileText className="size-4" />} />
        <StatTile label="Signed" value={String(signed)} hint="provenance-verified" icon={<ShieldCheck className="size-4" />} />
      </div>

      {skills.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-surface p-10 text-center">
          <Sparkles className="mx-auto size-6 text-muted" />
          <p className="mt-3 text-sm font-medium text-ink">No skills yet</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted">{SKILL_BLURB}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((sk) => (
            <Link key={sk.id} href={`/skills/${sk.id}`} className="card-elev lift block rounded-xl border border-border bg-surface p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-ink">{sk.name}</h3>
                  <p className="mt-0.5 font-mono text-xs text-muted">{sk.version}</p>
                </div>
                <Badge tone={statusTone(sk.status)}><StatusDot tone={statusTone(sk.status)} />{sk.status}</Badge>
              </div>

              <p className="mt-3 line-clamp-2 text-xs text-muted">{sk.description}</p>

              <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs">
                {sk.signature ? (
                  <span className="inline-flex items-center gap-1 text-good"><ShieldCheck className="size-3.5" /> signed</span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-muted"><ShieldAlert className="size-3.5" /> unsigned</span>
                )}
                <span className="inline-flex items-center gap-1 text-muted">
                  <FileText className="size-3.5" /> {sk.resources.length} file{sk.resources.length === 1 ? "" : "s"}
                </span>
              </div>

              <div className="mt-3 flex items-center justify-between text-xs text-muted">
                <span className="inline-flex items-center gap-1"><Users className="size-3.5" />{sk.boundAgents} agent{sk.boundAgents === 1 ? "" : "s"} bound</span>
                <span>updated {relative(sk.lastUpdated)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
