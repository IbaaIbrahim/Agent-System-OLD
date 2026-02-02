# Multi-stage build: build with Node, serve with nginx
FROM node:20-alpine AS build

WORKDIR /app

# Copy workspace root
COPY frontend/package.json frontend/package-lock.json ./

# Copy workspace packages
COPY frontend/packages/ ./packages/
COPY frontend/apps/ ./apps/

# Install dependencies
RUN npm ci

# Build the demo app (skip tsc — pre-existing type errors in chatbot-ui package)
RUN cd apps/demo && npx vite build

# Serve with nginx
FROM nginx:alpine

# Copy built assets from demo app
COPY --from=build /app/apps/demo/dist /usr/share/nginx/html

# Nginx config for SPA routing
RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
