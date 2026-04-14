FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY src/ ./src/
COPY tsconfig.json ./
COPY codegen.yml ./
RUN npm run codegen && npm run build && cp src/schema.graphql dist/schema.graphql

# Development stage — all deps (including devDeps) for codegen + hot reload
FROM node:20-alpine AS dev
WORKDIR /app
COPY package*.json ./
RUN npm ci
EXPOSE 4000

# Production stage — devDeps omitted, only compiled dist
FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=builder /app/dist ./dist
EXPOSE 4000
CMD ["node", "dist/index.js"]
