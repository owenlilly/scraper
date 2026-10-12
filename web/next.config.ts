import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  // pg runs only on the server; keep it out of the client bundle.
  serverExternalPackages: ["pg"],
  async headers() {
    return [
      {
        // The invitation token is in this page's address (after the #). Never send that address on as a Referer, and never cache the page.
        source: "/sign-up",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
    ];
  },
};

export default config;
