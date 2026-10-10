FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Type-checking (tsc -b) runs in the frontend CI job; it also covers a test that imports the backend
# OpenAPI file, which is outside this image's build context.
RUN npx vite build

# Unprivileged nginx: runs as uid 101 (nginx) and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
