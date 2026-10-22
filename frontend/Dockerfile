# syntax=docker/dockerfile:1

# --- 建置階段：npm ci + vite build，產出 dist/ 靜態檔 ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# --- 執行階段：只放靜態檔與 nginx 設定，不帶 node_modules ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
