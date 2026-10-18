/* Default brand mark (generic shield-with-check). Swap this SVG — or point `ORG.logo`
   in src/lib/org.ts at a different component — to rebrand. Inherits `currentColor`
   so it stays legible on the accent-colored chip in the app shell. */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-4Z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}
