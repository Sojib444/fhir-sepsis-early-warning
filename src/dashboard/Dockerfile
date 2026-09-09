# syntax=docker/dockerfile:1
# Phase 7 dashboard: static Angular bundle behind nginx, with /api reversed
# to the CDS service so the browser only ever talks to same-origin paths.

FROM node:22 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY . .
# The image build doubles as the lint + compile check for the dashboard.
RUN npm run lint
RUN npm run build

FROM nginx:1.27-alpine AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/dashboard/browser /usr/share/nginx/html
EXPOSE 80