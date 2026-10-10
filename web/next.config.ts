import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  // better-auth and pg run only on the server; keep them out of the client bundle.
  serverExternalPackages: ["pg"],
};

export default config;
