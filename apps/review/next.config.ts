import type { NextConfig } from "next";

import { API_URL } from "./lib/api";

const nextConfig: NextConfig = {
  // The scanned pages load in the browser, so their PNGs are proxied to the
  // engine's API; everything else is fetched on the server.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
