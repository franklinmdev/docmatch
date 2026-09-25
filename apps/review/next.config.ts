import type { NextConfig } from "next";

import { API_URL } from "./lib/api";

const nextConfig: NextConfig = {
  // The scanned pages load in the browser, so their PNGs alone are proxied to
  // the engine's API; everything else is fetched on the server. The scripts
  // bind to 127.0.0.1, as the API does (#164), since next's default is 0.0.0.0.
  async rewrites() {
    return [
      {
        source: "/api/documents/:id/pages/:page",
        destination: `${API_URL}/documents/:id/pages/:page`,
      },
    ];
  },
};

export default nextConfig;
