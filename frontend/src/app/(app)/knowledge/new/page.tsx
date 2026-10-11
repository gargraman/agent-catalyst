"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import { Button, Card, CardContent, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { Stepper } from "@/components/stepper";
import { Term } from "@/components/term";
import {
  SourcePicker, buildDataSource, finalizeCredential, hasConfiguredSource, isSourceComplete,
  summarizeSource, initialSourceState, type SourceState,
} from "@/components/source-picker";
import { addDataSource, addKnowledgeBase, genId, nowIso } from "@/lib/store";
import type { KnowledgeBase } from "@/lib/data";

const STEPS = ["Basics", "First source", "Review"];

/* @backend integration points (this page):
 *  - @backend POST /v1/knowledge-bases - create knowledge base [via addKnowledgeBase]
 *  - @backend POST /v1/knowledge-bases/:id/sources - first source, async ingest [via addDataSource]
 *  - @backend file upload needs a presigned-URL/upload endpoint (see src/components/source-picker.tsx @backend marker)
 * See src/lib/store.ts markers + docs/backend-integration-map.md for the authoritative contracts.
 */
export default function NewKnowledgeBasePage() {
  const router = useRouter();
  const [step, setStep] = React.useState(0);

  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [embeddingModel, setEmbeddingModel] = React.useState("text-embedding-3-large");
  const [indexType, setIndexType] = React.useState("HNSW");
  const [chunkSize, setChunkSize] = React.useState("800");
  const [overlap, setOverlap] = React.useState("120");

  const [source, setSource] = React.useState<SourceState>(initialSourceState);

  const canNext =
    step === 0 ? name.trim().length > 0
      : step === 1 ? isSourceComplete(source)
        : name.trim().length > 0;
  const hasSource = hasConfiguredSource(source);

  function finish() {
    const kb: KnowledgeBase = {
      id: genId("kb"),
      name: name.trim(),
      embeddingModel,
      docs: 0,
      chunks: 0,
      sizeMB: 0,
      lastSync: nowIso(),
      status: hasSource ? "indexing" : "ready",
      boundAgents: 0,
      sources: [],
    };
    addKnowledgeBase(kb);

    const src = buildDataSource(source);
    if (src) {
      const credentialRef = finalizeCredential(source);
      addDataSource(kb.id, credentialRef ? { ...src, config: { ...src.config, credentialRef } } : src);
    }

    router.push(`/knowledge/${kb.id}`);
  }

  return (
    <div className="space-y-6">
      <div>
        <Button variant="ghost" size="sm" href="/knowledge" className="-ml-2 mb-3">
          <ArrowLeft className="size-4" /> Knowledge
        </Button>
        <PageHeader title="New knowledge base" description="Create a knowledge base, pick an embedding model and add a first source." />
      </div>

      <Stepper steps={STEPS} current={step} />

      <Card>
        <CardContent className="space-y-5">
          {step === 0 && (
            <div className="space-y-5">
              <Field label="Name">
                <Input placeholder="Product Documentation" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Description" hint="Optional. What this knowledge base covers.">
                <Textarea
                  rows={3}
                  placeholder="Grounds agents in the Trellix product and configuration docs."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={<Term id="embedding-model">Embedding model</Term>}>
                  <Select value={embeddingModel} onChange={(e) => setEmbeddingModel(e.target.value)}>
                    <option value="text-embedding-3-large">text-embedding-3-large</option>
                    <option value="text-embedding-3-small">text-embedding-3-small</option>
                    <option value="voyage-3-large">voyage-3-large</option>
                  </Select>
                </Field>
                <Field label="Index type">
                  <Select value={indexType} onChange={(e) => setIndexType(e.target.value)}>
                    <option value="HNSW">HNSW</option>
                    <option value="IVF">IVF</option>
                  </Select>
                </Field>
                <Field label={<><Term id="chunk">Chunk</Term> size</>} hint="Tokens per chunk">
                  <Input type="number" min={1} value={chunkSize} onChange={(e) => setChunkSize(e.target.value)} />
                </Field>
                <Field label="Overlap" hint="Tokens shared between chunks">
                  <Input type="number" min={0} value={overlap} onChange={(e) => setOverlap(e.target.value)} />
                </Field>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <p className="text-sm text-muted">Add a first source now, or skip and add sources later.</p>
              <SourcePicker value={source} onChange={setSource} allowSkip />
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <h3 className="text-sm font-semibold text-ink">Review</h3>
              <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                <ReviewRow label="Name" value={name.trim() || "Untitled"} />
                <ReviewRow label="Embedding model" value={embeddingModel} mono />
                <ReviewRow label="Index type" value={indexType} />
                <ReviewRow label="Chunk size / overlap" value={`${chunkSize} / ${overlap}`} />
                <ReviewRow label="First source" value={summarizeSource(source)} />
                <ReviewRow label="Initial status" value={hasSource ? "Indexing" : "Ready"} />
              </dl>
              {description.trim() && (
                <div>
                  <div className="text-xs font-medium text-ink-2">Description</div>
                  <p className="mt-1 text-sm text-muted">{description.trim()}</p>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0}
        >
          <ArrowLeft className="size-4" /> Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button variant="primary" onClick={() => setStep((s) => s + 1)} disabled={!canNext}>
            Next <ArrowRight className="size-4" />
          </Button>
        ) : (
          <Button variant="primary" onClick={finish} disabled={!canNext}>
            <Check className="size-4" /> Create knowledge base
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

