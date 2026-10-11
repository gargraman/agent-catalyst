"use client";

import * as React from "react";
import { FileText, UploadCloud, X } from "lucide-react";
import { cn } from "@/lib/utils";

export function FileDropzone({
  onChange, hint = "PDF, DOCX, TXT, MD or HTML up to 25 MB each",
}: {
  onChange?: (files: File[]) => void;
  hint?: string;
}) {
  const [files, setFiles] = React.useState<File[]>([]);
  const [over, setOver] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  function add(list: FileList | null) {
    if (!list) return;
    const next = [...files, ...Array.from(list)];
    setFiles(next);
    onChange?.(next);
  }
  function remove(i: number) {
    const next = files.filter((_, idx) => idx !== i);
    setFiles(next);
    onChange?.(next);
  }

  return (
    <div className="space-y-2">
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); add(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-8 text-center transition-colors",
          over ? "border-accent bg-accent-soft" : "border-border bg-elevated hover:border-border-strong",
        )}
      >
        <UploadCloud className={cn("size-6", over ? "text-accent" : "text-muted")} />
        <p className="text-sm text-ink-2"><span className="font-medium text-ink">Click to upload</span> or drag and drop</p>
        <p className="text-xs text-muted">{hint}</p>
        <input ref={inputRef} type="file" multiple className="hidden" onChange={(e) => add(e.target.files)} />
      </div>
      {files.length > 0 && (
        <ul className="space-y-1.5">
          {files.map((f, i) => (
            <li key={i} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm">
              <FileText className="size-4 shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate text-ink-2">{f.name}</span>
              <span className="tnum text-xs text-muted">{(f.size / 1024).toFixed(0)} KB</span>
              <button onClick={(e) => { e.stopPropagation(); remove(i); }} className="text-muted hover:text-ink"><X className="size-4" /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
