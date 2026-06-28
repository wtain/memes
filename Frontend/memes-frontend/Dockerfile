FROM node:22-alpine AS builder
WORKDIR /app

RUN corepack enable pnpm

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# VITE_BACKEND_API_URL is baked into the bundle at build time.
# Pass it as a build arg: docker build --build-arg VITE_BACKEND_API_URL=https://api.example.com .
ARG VITE_BACKEND_API_URL=http://localhost:8000
ENV VITE_BACKEND_API_URL=${VITE_BACKEND_API_URL}

RUN pnpm build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
