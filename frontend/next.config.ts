import type { NextConfig } from "next";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

const config: NextConfig = {
  /**
   * The browser talks to Next; Next talks to FastAPI. That keeps SSE same-origin, so the event
   * stream needs no CORS preflight and no credentials fuss later.
   *
   * `BACKEND_URL` is read **at build time, not at start time**. Next serialises the resolved
   * rewrite into `.next/routes-manifest.json`, so `BACKEND_URL=... next start` is silently
   * ignored and the default is used. Set it when you build:
   *
   *     BACKEND_URL=https://api.example.com npm run build
   *
   * Getting this wrong is quiet: requests proxy to whatever is listening on the default port
   * and return that service's 404, which looks exactly like a routing bug in this app.
   */
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND}/api/:path*` }];
  },

  /**
   * Next's dev-mode rewrite proxy kills any request past 30s by default
   * ("we limit proxy requests to 30s by default, in development" — http-proxy's
   * `proxyTimeout`). The clause assistant can legitimately take longer than that
   * (multi-turn agent calls to the model), which surfaced as a raw
   * "socket hang up" / ECONNRESET on the client with no usable error message.
   * Bump it well past the slowest real assistant call.
   */
  experimental: {
    proxyTimeout: 120_000,
  },
};

export default config;