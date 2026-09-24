# syntax=docker/dockerfile:1.7

# --- build ------------------------------------------------------------------
FROM node:24-slim AS build

WORKDIR /app

# Public client values are compiled into the Next.js bundle (and the
# middleware) at build time, so they must be set before `yarn build`.
# compose.yaml passes them as build args; these are local-safe defaults.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB=50

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB=$NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB

# Dependencies first so the install layer is cached until package.json or
# yarn.lock change.
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

COPY . .

# `prebuild` copies the pdf.js runtime assets into public/pdfjs/<version>/.
# next.config.ts sets `output: "standalone"`: .next/standalone holds
# server.js plus only the node_modules files the server actually needs.
RUN yarn build


# --- runtime ----------------------------------------------------------------
FROM node:24-slim AS production

WORKDIR /app

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB=50

ENV NODE_ENV=production
# Already inlined by the build; kept in the environment for inspection.
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB=$NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB
# server.js binds to $HOSTNAME:$PORT. Docker sets HOSTNAME to the container
# id, so pin it to all interfaces.
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

# The standalone server does not serve .next/static or public/ unless they
# sit next to it, so copy them in explicitly.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
