# syntax=docker/dockerfile:1.7
# Use the official Node.js 24 slim image as the base image
FROM node:24-slim AS base


# Set the working directory inside the container
WORKDIR /app


# Public client values are compiled into the Next.js bundle.
# Keep local-safe defaults and override them with build args for production.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=
ARG NEXT_PUBLIC_POSTHOG_KEY=
ARG NEXT_PUBLIC_POSTHOG_HOST=
ARG UPLOAD_POSTHOG_SOURCEMAPS=false

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST
ENV UPLOAD_POSTHOG_SOURCEMAPS=$UPLOAD_POSTHOG_SOURCEMAPS


# Copy package.json and yarn.lock to the working directory
# This is done separately to leverage Docker cache.
# If these files haven't changed, Docker will use the cached layer for yarn install.
COPY package.json yarn.lock ./


# Install dependencies.
# Only run this if package.json or yarn.lock changed
# Using --frozen-lockfile for reproducible and potentially faster installs
RUN yarn install --frozen-lockfile && \
    if [ "$UPLOAD_POSTHOG_SOURCEMAPS" = "true" ]; then yarn global add @posthog/cli; fi

# Add yarn global bin to PATH
ENV PATH="/root/.yarn/bin:$PATH"


# Copy the rest of the application code to the working directory
# This layer will only be rebuilt if the application code changes.
# Ensure .dockerignore is properly configured to exclude unnecessary files.
COPY . .


# Build the Next.js application
# This will only run if the code (above) or dependencies (further above) changed.
RUN yarn build

# Optionally upload source maps to PostHog, then delete them from the build.
# This is disabled by default for local compose builds.
RUN --mount=type=secret,id=POSTHOG_ENV_ID \
    --mount=type=secret,id=POSTHOG_CLI_TOKEN \
    if [ "$UPLOAD_POSTHOG_SOURCEMAPS" = "true" ]; then \
        export POSTHOG_CLI_ENV_ID="$(cat /run/secrets/POSTHOG_ENV_ID)" && \
        export POSTHOG_CLI_TOKEN="$(cat /run/secrets/POSTHOG_CLI_TOKEN)" && \
        posthog-cli sourcemap inject --directory .next && \
        posthog-cli sourcemap upload --directory .next && \
        find .next -name '*.map' -delete; \
    fi


# Use the official Node.js 24 slim image for running the app
FROM node:24-slim AS production


# Set the working directory
WORKDIR /app

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=
ARG NEXT_PUBLIC_POSTHOG_KEY=
ARG NEXT_PUBLIC_POSTHOG_HOST=

# Set production environment variables
ENV NODE_ENV=production
# Keep these available in the production stage for runtime checks and logs.
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY
ENV NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST


# Copy package metadata first so the production dependency install can be cached
# independently of the Next.js build output.
COPY --from=base /app/package.json ./
COPY --from=base /app/yarn.lock ./


# Install only the production dependencies
# This ensures a smaller final image by not including devDependencies
RUN yarn install --production --frozen-lockfile


# Copy the built application from the base image after dependencies are installed.
COPY --from=base /app/.next ./.next
COPY --from=base /app/public ./public


# Expose port 3000 to the outside world
EXPOSE 3000


# Define the command to start the Next.js application
CMD ["yarn", "start"]
