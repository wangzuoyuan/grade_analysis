# 前端：Next.js standalone 产物（构建上下文 = frontend/）
FROM node:20-slim AS build
WORKDIR /app
# 国内网络：npm 走淘宝镜像，避免连 registry.npmjs.org 超时
RUN npm config set registry https://registry.npmmirror.com
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# public 目录可能不存在，先兜底建一个，保证后续 COPY 不失败
RUN mkdir -p public && npm run build

FROM node:20-slim AS run
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
