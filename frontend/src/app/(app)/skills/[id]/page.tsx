"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeft, Sparkles, ShieldCheck, ShieldAlert, Users, FileText, Pencil, Trash2, Link2, Ban, Lock, BadgeCheck,
} from "lucide-react";
import {
  Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle,
  EmptyState, Field, Select, StatusDot, Textarea,
} from "@/components/ui";
import { Dialog, ConfirmDialog } from "@/components/dialog";
import { StatTile } from "@/components/stat-tile";
import { GateButton } from "@/components/gate";
import { AccessNote } from "@/components/access-note";
import { AccessPolicyEditor } from "@/components/access-policy-editor";
import { useAuth, usePrincipal } from "@/lib/auth";
import { canManage, canUse } from "@/lib/access-policy";
import {
  deleteSkill, publishSkill, updateSkill, updateAgent, toast, useAgents, useSkill,
} from "@/lib/store";
import type { Skill } from "@/lib/data";
import { relative } from "@/lib/format";

const statusTone = (s: Skill["status"]) => (s === "published" ? "good" : s === "deprecated" ? "neutral" : "warning");

/* @backend integration points (this page):
 *  - @backend GET /v1/skills/:id - skill detail (instructions, resources, signing, access) [via useSkill]
 *  - @backend PUT /v1/skills/:id - edit instructions/resources (content change flags bound agents) [via updateSkill]
 *  - @backend POST /v1/skills/:id/publish - sign + publish so it becomes bindable [via publishSkill]
 *  - @backend DELETE /v1/skills/:id - delete the skill (bindings become dangling refs) [via deleteSkill]
 *  - @backend PATCH /v1/agents/:id - bind this skill to an agent version [via updateAgent]
 *  - @backend PUT /v1/skills/:id/access-policy - per-resource access edits [via setAccessPolicy]
 * See docs/backend-integration-map.md for the authoritative contracts.
 */
export default function SkillDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);
  const skill = useSkill(id);
  const agents = useAgents();
  const { can } = useAuth();
  const principal = usePrincipal();

  const [editOpen, setEditOpen] = React.useState(false);
  const [editDraft, setEditDraft] = React.useState("");
  const [bindOpen, setBindOpen] = React.useState(false);
  const [bindAgent, setBindAgent] = React.useState("");
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [deprecateOpen, setDeprecateOpen] = React.useState(false);
  // One confirm for the re-eval cascade: publishing or saving a content change re-freezes the skill's
  // content identity, so every bound agent needs re-evaluation. Names the count before applying.
  const [reEval, setReEval] = React.useState<{ kind: "publish" | "save"; instructions?: string } | null>(null);

  const bindableAgents = agents.filter((a) => canManage(a.accessPolicy, principal));
  const effectiveBindAgent = bindAgent || bindableAgents.find((a) => !(a.skills ?? []).includes(id))?.id || bindableAgents[0]?.id || "";

  if (!skill) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/skills" className="-ml-2"><ArrowLeft className="size-4" /> Skills</Button>
        <EmptyState icon={<Sparkles className="size-5" />} title="Skill not found" hint="It may have been deleted." />
      </div>
    );
  }
  if (!canUse(skill.accessPolicy, principal)) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/skills" className="-ml-2"><ArrowLeft className="size-4" /> Skills</Button>
        <EmptyState icon={<Lock className="size-5" />} title="No access" hint="This skill is restricted by its access policy." />
      </div>
    );
  }

  const manage = can("skill.manage") && canManage(skill.accessPolicy, principal);
  const manageTitle = manage ? undefined : "Restricted by this skill's access policy";
  const bound = skill.boundAgents;

  function remove() {
    deleteSkill(id);
    router.push("/skills");
  }
  function openEdit() {
    setEditDraft(skill?.instructions ?? "");
    setEditOpen(true);
  }
  // Saving an instruction change: if agents are bound, route through the re-eval confirm first.
  function saveEdit() {
    setEditOpen(false);
    if (bound > 0) { setReEval({ kind: "save", instructions: editDraft }); return; }
    updateSkill(id, { instructions: editDraft });
  }
  // Publish & sign: if agents are bound (a re-publish), confirm the re-eval cascade first.
  function requestPublish() {
    if (bound > 0) { setReEval({ kind: "publish" }); return; }
    publishSkill(id);
  }
  function applyReEval() {
    if (!reEval) return;
    if (reEval.kind === "publish") publishSkill(id);
    else updateSkill(id, { instructions: reEval.instructions ?? skill?.instructions ?? "" });
    setReEval(null);
  }
  function submitBind() {
    const agent = agents.find((a) => a.id === effectiveBindAgent);
    if (!agent || !skill || !canManage(agent.accessPolicy, principal)) return;
    if ((agent.skills ?? []).includes(skill.id)) {
      toast(`${agent.name} already has "${skill.name}"`, "neutral");
      setBindOpen(false);
      return;
    }
    updateAgent(agent.id, { skills: [...(agent.skills ?? []), skill.id] }, false);
    toast(`Bound "${skill.name}" to ${agent.name}`);
    setBindOpen(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/skills" className="-ml-2 mb-3"><ArrowLeft className="size-4" /> Skills</Button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="flex items-center gap-2 text-xl font-semibold text-ink"><Sparkles className="size-5 text-muted" />{skill.name}</h1>
              <Badge tone={statusTone(skill.status)}><StatusDot tone={statusTone(skill.status)} />{skill.status}</Badge>
              <Badge tone="neutral">{skill.version}</Badge>
              {skill.signature ? (
                <Badge tone="good" title={`Signed by ${skill.signature.signedBy}`}><ShieldCheck className="size-3" /> signed</Badge>
              ) : (
                <Badge tone="warning"><ShieldAlert className="size-3" /> unsigned</Badge>
              )}
            </div>
            <p className="mt-1 max-w-2xl text-sm text-muted">{skill.description}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <GateButton permission="skill.manage" disabled={!manage} title={manageTitle} variant="secondary" size="sm" onClick={openEdit}>
              <Pencil className="size-4" /> Edit instructions
            </GateButton>
            {skill.status !== "published" ? (
              <GateButton permission="skill.manage" disabled={!manage} title={manageTitle} variant="primary" size="sm" onClick={requestPublish}>
                <BadgeCheck className="size-4" /> Publish &amp; sign
              </GateButton>
            ) : (
              <GateButton permission="skill.manage" disabled={!manage} title={manageTitle} variant="outline" size="sm" onClick={() => setDeprecateOpen(true)}>
                <Ban className="size-4" /> Deprecate
              </GateButton>
            )}
            <GateButton permission="agent.edit" variant="outline" size="sm" onClick={() => setBindOpen(true)} disabled={skill.status !== "published"} title={skill.status !== "published" ? "Only published skills can be bound" : undefined}>
              <Link2 className="size-4" /> Bind to agent
            </GateButton>
            <GateButton permission="skill.manage" disabled={!manage} title={manageTitle} variant="outline" size="sm" onClick={() => setDeleteOpen(true)}>
              <Trash2 className="size-4" /> Delete
            </GateButton>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="Bound agents" value={String(skill.boundAgents)} icon={<Users className="size-4" />} />
        <StatTile label="Bundled files" value={String(skill.resources.length)} icon={<FileText className="size-4" />} />
        <StatTile label="Version" value={skill.version} icon={<Sparkles className="size-4" />} />
        <StatTile label="Last updated" value={relative(skill.lastUpdated)} icon={<Pencil className="size-4" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader>
              <div><CardTitle>Instructions</CardTitle><CardDescription>The on-invoke body injected into the prompt when the skill applies (treated as untrusted content).</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-elevated p-3 font-mono text-xs leading-relaxed text-ink-2">{skill.instructions}</pre>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div><CardTitle>Bundled files</CardTitle><CardDescription>Reference material shipped with the skill — data the agent can consult, never executed.</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              {skill.resources.length === 0 ? (
                <EmptyState icon={<FileText className="size-5" />} title="No files" hint="This skill ships no bundled resources." />
              ) : (
                <div className="divide-y divide-border">
                  {skill.resources.map((r) => (
                    <div key={r.name} className="flex items-center justify-between gap-3 py-2.5">
                      <span className="flex min-w-0 items-center gap-2">
                        <FileText className="size-4 shrink-0 text-muted" />
                        <span className="truncate font-mono text-sm text-ink-2">{r.name}</span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        {r.sizeKB != null && <span className="text-xs text-muted">{r.sizeKB} KB</span>}
                        <Badge tone={r.kind === "instruction" ? "accent" : "neutral"}>{r.kind}</Badge>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <div><CardTitle>Signing &amp; provenance</CardTitle><CardDescription>Published skills are signed; agents only bind signed skills.</CardDescription></div>
            </CardHeader>
            <CardContent className="pt-0">
              {skill.signature ? (
                <div className="space-y-0">
                  <Row label="Status" value={<span className="inline-flex items-center gap-1 text-good"><ShieldCheck className="size-3.5" /> Signed</span>} />
                  <Row label="Signed by" value={skill.signature.signedBy} />
                  <Row label="Signed at" value={relative(skill.signature.signedAt)} />
                  <Row label="Signature" value={<span className="font-mono text-xs">{skill.signature.masked}</span>} />
                  <Row label="Content hash" value={<span className="font-mono text-xs">{skill.contentHash ?? "—"}</span>} />
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="flex items-center gap-1.5 text-sm text-warning"><ShieldAlert className="size-4" /> Unsigned draft — not bindable.</p>
                  <p className="text-xs text-muted">Publish &amp; sign to stamp provenance and make this skill bindable to agents.</p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div><CardTitle>Access</CardTitle><CardDescription>Who may use and manage this skill.</CardDescription></div>
            </CardHeader>
            <CardContent className="space-y-4 pt-0">
              <AccessNote variant="run" />
              <AccessPolicyEditor kind="skill" id={skill.id} policy={skill.accessPolicy} readOnly={!can("access.manage")} />
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Edit instructions */}
      <Dialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title="Edit instructions"
        description={`SKILL.md body for ${skill.name}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditOpen(false)}>Cancel</Button>
            <GateButton permission="skill.manage" variant="primary" onClick={saveEdit} disabled={!editDraft.trim()}>Save</GateButton>
          </>
        }
      >
        <div className="space-y-3">
          {bound > 0 && (
            <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
              {bound} agent{bound === 1 ? "" : "s"} bind this skill — saving changes will require them to be re-evaluated before they can publish.
            </p>
          )}
          <Textarea rows={14} className="font-mono text-xs" value={editDraft} onChange={(e) => setEditDraft(e.target.value)} />
          <p className="text-xs text-muted">Treated as untrusted content injected into the prompt — never include secrets.</p>
        </div>
      </Dialog>

      {/* Bind to agent */}
      <Dialog
        open={bindOpen}
        onClose={() => setBindOpen(false)}
        title="Bind to agent"
        description={`Attach ${skill.name} to an agent`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setBindOpen(false)}>Cancel</Button>
            <GateButton permission="agent.edit" variant="primary" onClick={submitBind} disabled={!effectiveBindAgent}>Bind</GateButton>
          </>
        }
      >
        {bindableAgents.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted">No agents you can manage in this workspace.</p>
        ) : (
          <Field label="Agent">
            <Select value={effectiveBindAgent} onChange={(e) => setBindAgent(e.target.value)}>
              {bindableAgents.map((a) => (
                <option key={a.id} value={a.id} disabled={(a.skills ?? []).includes(id)}>
                  {a.name}{(a.skills ?? []).includes(id) ? " (already bound)" : ""}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </Dialog>

      {/* Re-eval cascade confirm (publish or content save while bound) */}
      <ConfirmDialog
        open={!!reEval}
        onClose={() => setReEval(null)}
        onConfirm={applyReEval}
        title={reEval?.kind === "publish" ? "Publish & sign skill?" : "Save skill changes?"}
        confirmLabel={reEval?.kind === "publish" ? "Publish & sign" : "Save"}
        tone="primary"
        body={
          <p>
            {bound} agent{bound === 1 ? "" : "s"} bind <span className="font-medium text-ink">{skill.name}</span>.
            {" "}Re-freezing its content will flag {bound === 1 ? "that agent" : "those agents"} for re-evaluation — they must pass a fresh eval before they can publish again.
          </p>
        }
      />

      {/* Deprecate */}
      <ConfirmDialog
        open={deprecateOpen}
        onClose={() => setDeprecateOpen(false)}
        onConfirm={() => updateSkill(id, { status: "deprecated" })}
        title="Deprecate skill?"
        confirmLabel="Deprecate"
        tone="primary"
        body={
          <p>
            Deprecating <span className="font-medium text-ink">{skill.name}</span> removes it from the bindable list for new agents.
            Existing bound agents keep resolving it until they unbind.
          </p>
        }
      />

      {/* Delete */}
      <ConfirmDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={remove}
        title="Delete skill"
        confirmLabel="Delete"
        tone="danger"
        body={
          <>
            Delete <span className="font-medium text-ink">{skill.name}</span>?
            {bound > 0 && (
              <span className="mt-2 block text-critical">
                {bound} {bound === 1 ? "agent" : "agents"} bind this skill — those bindings will stop resolving.
              </span>
            )}
          </>
        }
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border/70 py-2.5 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-right text-sm font-medium text-ink-2">{value}</span>
    </div>
  );
}
