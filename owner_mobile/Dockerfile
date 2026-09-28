FROM node:22-alpine AS build

ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ARG VITE_CONTROL_PLANE_URL=https://brain.apizz.cc.cd
ENV VITE_CONTROL_PLANE_URL=${VITE_CONTROL_PLANE_URL}

WORKDIR /src/owner_mobile
COPY owner_mobile/package.json owner_mobile/package-lock.json ./
RUN npm ci
COPY owner_mobile/ ./
RUN npm run build

FROM nginx:1.27-alpine
ARG APP_VERSION=dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="Commerce Brain Owner Mobile" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}"
COPY owner_mobile/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/owner_mobile/dist /usr/share/nginx/html

EXPOSE 80
