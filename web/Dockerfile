# The site: a Next.js app served by its own Node process.
#
# nginx sits in front of this container (see deploy/nginx/nginx.conf) and hands
# it everything except the API paths, so the whole prototype stays on one
# origin and the browser never makes a cross-origin request.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# next.config.mjs rewrites are resolved during `next build` and frozen into the
# route manifest, so the API origin is a build argument. Passing it at run time
# would silently do nothing. The default matches the compose service name.
ARG PUSHPAK_API_ORIGIN=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 \
    PUSHPAK_API_ORIGIN=$PUSHPAK_API_ORIGIN
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
# next start needs the build output, the config it was built with, and the
# runtime half of node_modules. Nothing else from the build stage is used.
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.mjs ./next.config.mjs
USER node
EXPOSE 3000
CMD ["npx", "next", "start"]
