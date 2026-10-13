import type { NextConfig } from "next";

const backendOrigin = process.env.BACKEND_ORIGIN?.replace(/\/$/, "");

const config: NextConfig = {
  devIndicators: false,
  output: "standalone",
  async rewrites() {
    if (!backendOrigin) return [];
    return [
      { source: "/api/v1/:path*", destination: `${backendOrigin}/api/v1/:path*` },
      { source: "/health", destination: `${backendOrigin}/health` },
    ];
  },
};

export default config;
