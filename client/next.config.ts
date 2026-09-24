/** @type {import('next').NextConfig} */
const nextConfig = {
    // Self-contained server bundle in `<distDir>/standalone` (server.js + the
    // traced node_modules subset); the Docker image ships only that, plus
    // `<distDir>/static` and `public/`. Doesn't affect `next dev`.
    output: 'standalone' as const,
    // Separate build output for concurrent local reader benchmarks.
    distDir: process.env.NEXT_DIST_DIR || '.next',
    // Enable source maps in production for error tracking
    productionBrowserSourceMaps: true,
    // Transpile packages that import CSS from node_modules
    transpilePackages: ['pdfjs-dist'],
    async headers() {
        return [
            {
                // pdf.js worker/cmaps/fonts/wasm live under a directory named after
                // the pdfjs-dist version (scripts/sync-pdfjs-assets.mjs), so the
                // contents of any given path never change — cache them forever.
                // Next's default for public/ is `max-age=0`, which mobile browsers
                // don't reliably revalidate for worker scripts.
                source: '/pdfjs/:version/:path+',
                headers: [
                    { key: 'Cache-Control', value: 'public, max-age=31536000, immutable' },
                ],
            },
        ]
    },
    // Add image remote patterns configuration
    images: {
        remotePatterns: [
            {
                protocol: 'https' as const,
                hostname: 'assets.khoj.dev',
                port: '',
                pathname: '/**',
            },
            {
                protocol: 'https' as const,
                hostname: 'openpaper.ai',
                port: '',
                pathname: '/**',
            },
            {
                protocol: 'https' as const,
                hostname: 'lh3.googleusercontent.com',
                port: '',
                pathname: '/**',
            }
        ],
    },
}

export default nextConfig
