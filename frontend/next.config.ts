import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // No rewrites for /ws — the browser connects directly to the backend wss:// URL
  // (Vercel cannot proxy WebSocket upgrades on the free tier).
};

export default nextConfig;
