# Landing page: Vite/React build served by nginx, which also proxies the API
# so the whole prototype is reachable on a single origin.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy_params_pushpak /etc/nginx/proxy_params_pushpak
EXPOSE 80
