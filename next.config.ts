import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  assetPrefix: process.env.ASSET_PREFIX || undefined,
};

export default nextConfig;
