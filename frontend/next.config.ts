import type { NextConfig } from "next";

// Backend origin for the same-origin API proxy below. Server-side only (no NEXT_PUBLIC_ prefix) — it is read
// when the rewrite table is built, never shipped to the browser. Defaults to the local dev stack (make up → :8000).
const API_ORIGIN = process.env.AC_API_ORIGIN ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Proxy the backend's console API through the Next origin. The browser only ever talks to its own origin, so:
  //   - there is no CORS surface on the API (it deliberately has no CORS middleware);
  //   - the real OIDC cookie session + double-submit CSRF (feature 019) work unchanged when they replace dev auth.
  // Only /api/v1/* is proxied — /healthz, /metrics and the published /v1 invoke plane are not console traffic.
  // TODO(auth): when 019 OIDC login replaces dev headers, also proxy `/auth/:path*` (login/callback/logout) so the
  //   session + `ac_csrf` cookies are set on THIS origin; otherwise the browser never sends them to /api/v1.
  // TODO(deploy): in a deployed build, set AC_API_ORIGIN at BUILD time (rewrites are compiled into the routes
  //   manifest) or front both with one ingress and drop the rewrite.
  // The rewrite proxy cuts an upstream socket that's been idle for `proxyTimeout` (default 30 s — and `0` also means
  // 30 s, see next/dist/server/lib/router-utils/proxy-request.js). The invoke stream can legitimately be silent for
  // the server's whole 30 s deadline (slow first token / reasoning), and then must still deliver its `error`+`done`
  // frames — at the default the proxy wins the race and the browser sees "connection lost" (026 L10 live finding).
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${API_ORIGIN}/api/v1/:path*` }];
  },
};

export default nextConfig;
