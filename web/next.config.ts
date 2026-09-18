import type { NextConfig } from "next";

// Exported as static files into web/out, which the FastAPI app serves at http://localhost:8000.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
