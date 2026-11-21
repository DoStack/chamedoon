# Self-contained image of the public site (production runs the same server config with the build mounted,
# see deploy/docker-compose.yml, so nightly data rebuilds need no image rebuild).
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY dist /srv/site/current
EXPOSE 8080
