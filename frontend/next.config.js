/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 精简运行时镜像：产出自带 node server 的 standalone 包
  output: 'standalone',
  async rewrites() {
    // 仅本地 dev 生效：把 /api 代理到后端。
    // 生产（NAS）由 compose 内的 Caddy 按路径分流，请求不经过 Next。
    const backend = process.env.BACKEND_INTERNAL_URL || 'http://localhost:8000'
    return [
      {
        source: '/api/:path*',
        destination: `${backend}/api/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
