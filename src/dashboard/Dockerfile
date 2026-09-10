# syntax=docker/dockerfile:1
# Phase 7 dashboard: static Angular bundle behind nginx, with /api reversed
# to the CDS service so the browser only ever talks to same-origin paths.
# Base images are pinned by digest (§23), multi-arch manifest lists.

FROM node:22@sha256:8a34c4ab3ea2c5cd194f07e317b2a8f09461d3c8b05c4e34c8ccd56d56024c4d AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY . .
# The image build doubles as the lint + compile check for the dashboard.
RUN npm run lint
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/dashboard/browser /usr/share/nginx/html
EXPOSE 80