import type { NextConfig } from "next";

// NEXT_PUBLIC_* values are inlined at build time; if they are unset the code
// falls back to localhost URLs, which would ship silently in a prod bundle.
const missing = ["NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_WS_URL"].filter(
  (name) => !process.env[name],
);
if (missing.length > 0) {
  const message = `${missing.join(", ")} unset — localhost fallbacks will be baked into this build`;
  if (process.env.VERCEL_ENV === "production") {
    throw new Error(`Refusing production build: ${message}`);
  }
  console.warn(`[next.config] ${message}`);
}

const nextConfig: NextConfig = {
  // No rewrites for /ws — the browser connects directly to the backend wss:// URL
  // (Vercel cannot proxy WebSocket upgrades on the free tier).
};

export default nextConfig;
