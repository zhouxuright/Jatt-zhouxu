# =============================================================================
# Legal Intelligent Assistance System - Frontend Dockerfile
# =============================================================================
# Multi-stage build: Vue3 app built with Vite, served by Nginx.
#
# Build:
#   docker build -t legal-frontend -f frontend/Dockerfile frontend/
#   docker run -p 3000:80 legal-frontend
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the Vue3 application
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files first for better layer caching
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci --silent

# Copy source code
COPY . .

# Build the application
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Serve with Nginx
# ---------------------------------------------------------------------------
FROM nginx:alpine AS runtime

# Remove default nginx configuration
RUN rm -f /etc/nginx/conf.d/default.conf

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built files from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Expose port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost:80/ || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]