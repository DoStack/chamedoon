import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloudflare quick tunnels (and Telegram WebView) hit the Next.js *dev* server
  // from a non-localhost origin. Without this, /_next assets return Unauthorized
  // and the Mini App stays on "Loading…".
  allowedDevOrigins: ["*.trycloudflare.com"],
  async headers() {
    return [
      {
        source: "/app",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org;",
          },
        ],
      },
      {
        source: "/app/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value:
              "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org;",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
