"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, FileText, Pencil } from "lucide-react";
import { Button, Card, CardContent, EmptyState, Field, Input, PageHeader, Textarea, Badge } from "@/components/ui";
import { Stepper } from "@/components/stepper";
import { FileDropzone } from "@/components/file-dropzone";
import { requirementText } from "@/components/gate";
import { addSkill, publishSkill, genId, nowIso } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import { slugify } from "@/lib/iam";
import type { Skill, SkillResource } from "@/lib/data";
import { SKILL_BLURB } from "../page";

const STEPS = ["Basics", "Instructions", "Resources", "Review"];

/* @backend integration points (this page):
 *  - @backend POST /v1/skills - create a draft skill [via addSkill]
 *  - @backend POST /v1/skills/:id/publish - sign + publish so it becomes bindable [via publishSkill]
 *  - @backend POST /v1/skills/:id/resources - upload bundled reference files (presigned upload; mocked here)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function NewSkillPage() {
  const router = useRouter();
  const { can } = useAuth();
  const [step, setStep] = React.useState(0);

  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [instructions, setInstructions] = React.useState("");
  const [files, setFiles] = React.useState<File[]>([]);
  const [publishNow, setPublishNow] = React.useState(true);

  // Self-guard (mirrors agents/new): skill.manage is freeze-aware, so this also blocks creating in an
  // Inactive (read-only) workspace, not just insufficient-role callers.
  if (!can("skill.manage")) {
    return (
      <div className="space-y-6">
        <Button variant="ghost" size="sm" href="/skills" className="-ml-2"><ArrowLeft className="size-4" /> Skills</Button>
        <EmptyState icon={<FileText className="size-5" />} title="You can't create skills here" hint={requirementText("skill.manage")} />
      </div>
    );
  }

  const canNext =
    step === 0 ? name.trim().length > 0 && description.trim().length > 0
      : step === 1 ? instructions.trim().length > 0
        : true;

  // SKILL.md is the instruction body (step 2), bundled automatically — its size tracks the instruction text.
  const skillMdSizeKB = Math.max(0.5, Math.round((instructions.length / 1024) * 10) / 10);

  // Accept the dropzone's files, de-duping by name and reserving "SKILL.md" for the instruction body
  // (both keep the detail page's `key={r.name}` unique).
  function onFiles(next: File[]) {
    const seen = new Set<string>();
    const deduped = next.filter((f) => {
      if (f.name === "SKILL.md" || seen.has(f.name)) return false;
      seen.add(f.name);
      return true;
    });
    setFiles(deduped);
  }

  function finish() {
    const id = genId("skill");
    const resources: SkillResource[] = [
      { name: "SKILL.md", kind: "instruction", sizeKB: skillMdSizeKB },
      ...files.map((f) => ({
        name: f.name,
        kind: "reference" as const,
        sizeKB: Math.max(0.1, Math.round((f.size / 1024) * 10) / 10),
        contentHash: `sha256:${genId("res")}`,
      })),
    ];
    const skill: Skill = {
      id,
      name: name.trim(),
      slug: slugify(name),
      description: description.trim(),
      instructions: instructions.trim(),
      resources,
      status: "draft",
      version: "v1",
      contentHash: `sha256:${id}-v1`,
      signature: null,
      boundAgents: 0,
      lastUpdated: nowIso(),
    };
    // When publishing immediately, suppress the "created as a draft" toast so only "published & signed" shows.
    addSkill(skill, !publishNow);
    if (publishNow) publishSkill(id);
    router.push(`/skills/${id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/skills" className="-ml-2 mb-3"><ArrowLeft className="size-4" /> Skills</Button>
        <PageHeader title="New skill" description="Package reusable instructions and reference files that agents can attach." />
      </div>

      <p className="-mt-2 max-w-3xl text-sm text-muted">{SKILL_BLURB}</p>

      <Stepper steps={STEPS} current={step} />

      <Card>
        <CardContent className="space-y-5">
          {step === 0 && (
            <div className="space-y-5">
              <Field label="Name">
                <Input placeholder="SOC Triage Playbook" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Description" hint="Always-loaded summary. The model reads this to DECIDE when to apply the skill — keep it specific.">
                <Textarea
                  rows={2}
                  placeholder="Step-by-step alert-triage method: correlate signals, assign severity, draft a verdict."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </Field>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <Field label="Instructions (SKILL.md)" hint="The on-invoke body — the actual playbook injected into the prompt when the skill applies. Markdown is fine.">
                <Textarea
                  rows={12}
                  className="font-mono text-xs"
                  placeholder={"When triaging an alert:\n1. Correlate against recent related signals before judging severity.\n2. Map behavior to MITRE ATT&CK where possible.\n3. Assign a severity (P1–P4) with a one-line justification.\nNever invent indicators not present in the evidence."}
                  value={instructions}
                  onChange={(e) => setInstructions(e.target.value)}
                />
              </Field>
              <p className="text-xs text-muted">
                Skill instructions are treated as untrusted content injected into the system prompt — never put secrets or credentials here.
              </p>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-5">
              {/* SKILL.md — the instruction body (step 2), always bundled. A first-class, editable item. */}
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">Instruction file</p>
                <div className="rounded-xl border border-border bg-elevated p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <FileText className="size-4 shrink-0 text-muted" />
                      <span className="truncate font-mono text-sm text-ink-2">SKILL.md</span>
                      <Badge tone="accent">instruction</Badge>
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="tnum text-xs text-muted">{skillMdSizeKB} KB</span>
                      <Button variant="ghost" size="sm" onClick={() => setStep(1)}><Pencil className="size-3.5" /> Edit instructions</Button>
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-muted">Generated from your Instructions — the body the model reads when the skill applies.</p>
                  {instructions.trim() && (
                    <p className="mt-2 line-clamp-2 whitespace-pre-wrap border-t border-border/70 pt-2 font-mono text-[11px] leading-relaxed text-muted">{instructions.trim()}</p>
                  )}
                </div>
              </div>

              {/* Reference files — bundled data the agent can consult (not executable). Metadata only (mock). */}
              <div>
                <p className="mb-2 text-xs font-medium text-ink-2">Reference files <span className="font-normal text-muted">(optional)</span></p>
                <FileDropzone
                  value={files}
                  onChange={onFiles}
                  hint="Drag & drop or browse — data the agent can consult (not executable). SKILL.md is reserved."
                />
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-ink">Review</h3>
              <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                <ReviewRow label="Name" value={name.trim() || "Untitled"} />
                <ReviewRow label="Version" value="v1" mono />
                <ReviewRow label="Bundled files" value={`SKILL.md + ${files.length} reference${files.length === 1 ? "" : "s"}`} />
                <ReviewRow label="On publish" value={publishNow ? "Published & signed" : "Saved as draft"} />
              </dl>
              <div>
                <div className="text-xs font-medium text-ink-2">Description</div>
                <p className="mt-1 text-sm text-muted">{description.trim() || "—"}</p>
              </div>
              <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-border bg-elevated p-3">
                <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={publishNow} onChange={(e) => setPublishNow(e.target.checked)} />
                <span className="text-xs text-ink-2">
                  <span className="font-medium">Publish &amp; sign now</span> — makes the skill bindable to agents. Leave unchecked to keep it a draft (drafts can&apos;t be bound).
                </span>
              </label>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <Button variant="outline" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
          <ArrowLeft className="size-4" /> Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button variant="primary" onClick={() => setStep((s) => s + 1)} disabled={!canNext}>
            Next <ArrowRight className="size-4" />
          </Button>
        ) : (
          <Button variant="primary" onClick={finish} disabled={!canNext}>
            <Check className="size-4" /> {publishNow ? "Create, publish & sign" : "Create draft"}
          </Button>
        )}
      </div>
    </div>
  );
}

function ReviewRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`mt-0.5 text-ink ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}
