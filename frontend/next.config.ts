import type { NextConfig } from "next";

const config: NextConfig = { devIndicators: false, output: "standalone",
  async rewrites(){
    const backend=process.env.INTERNAL_API_URL||'http://backend:8001';
    return ['/api/:path*','/health','/ready'].map(source=>({source,destination:`${backend}${source}`}));
  },
};

export default config;
