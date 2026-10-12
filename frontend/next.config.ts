import type { NextConfig } from "next";

const backendUrl = process.env.RENDER_BACKEND_URL?.trim().replace(/\/+$/, "");

const config: NextConfig = {
  devIndicators: false,
  output: "standalone",
  async rewrites() {
    if (!backendUrl) {
      if (process.env.VERCEL) throw new Error("RENDER_BACKEND_URL is required on Vercel");
      return [];
    }
    if (!/^https?:\/\//.test(backendUrl)) {
      throw new Error("RENDER_BACKEND_URL must be an http(s) URL");
    }
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/health", destination: `${backendUrl}/health` },
    ];
  },
};

export default config;
