/**
 * The site and the API are one origin.
 *
 * In production nginx sits in front and hands /api, /dashboard, /docs,
 * /openapi.json and /static to the FastAPI container before Next ever sees
 * them. These rewrites reproduce that in `next dev`, so the site behaves
 * identically whether you run it behind the proxy or on its own — and so the
 * client can keep using bare relative paths like /api/v1/index.
 *
 * PUSHPAK_API_ORIGIN points at the API. Next resolves rewrites() at BUILD time
 * and writes the destinations into .next/routes-manifest.json, so `next start`
 * never reads this variable — setting it in the container's environment does
 * nothing. It has to be a build argument, which is how web/Dockerfile passes
 * it. `next dev` re-evaluates this file on every start, so the value is live
 * there and scripts/dev.sh just exports it.
 *
 * It is a server-side value either way: not NEXT_PUBLIC_, never in the bundle.
 */
const API = process.env.PUSHPAK_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig = {
  reactStrictMode: true,
  // Pin the workspace root. Without it Turbopack walks up past the repository
  // looking for a lockfile and warns about an unrelated one in $HOME.
  turbopack: { root: import.meta.dirname },
  // Next 16 writes AGENTS.md and CLAUDE.md into the project on first run. This
  // repo documents itself in README.md; it does not want two more files it did
  // not author appearing in `git status`.
  agentRules: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/dashboard", destination: `${API}/dashboard` },
      { source: "/static/:path*", destination: `${API}/static/:path*` },
      { source: "/docs", destination: `${API}/docs` },
      { source: "/openapi.json", destination: `${API}/openapi.json` },
    ];
  },
};

export default nextConfig;
