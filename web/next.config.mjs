// El navegador solo habla con este origen (/api/*); Next hace de proxy hacia la API interna.
// Así la cookie de sesión es same-origin y ninguna clave de proveedor llega al bundle.
const API_INTERNAL_URL = process.env.API_INTERNAL_URL || "http://api:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  experimental: { proxyTimeout: 90000 },
  async rewrites() {
    return [
      // Playbook de IDE en el origen público (/v1/ide/…) sin mover el resto de /v1.
      { source: "/v1/ide/:path*", destination: `${API_INTERNAL_URL}/v1/ide/:path*` },
      { source: "/api/:path*", destination: `${API_INTERNAL_URL}/:path*` },
    ];
  },
};
export default nextConfig;
