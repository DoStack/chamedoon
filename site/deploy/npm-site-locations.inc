# Public site (Astro static, container chamedoon-site). Included from chamedoon-web-locations.inc BEFORE `location /`.
# Only these paths go to the site; /, /app/, /api/, /admin/, /static/, /feeds/, /ingest/, /browse/ stay on chamedoon-web.
set $site chamedoon-site:8080;
location /fa/ { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location /en/ { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location /_astro/ { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location /data/ { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location /media/site/ { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /robots.txt { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /llms.txt { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /llms-full.txt { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /sitemap-index.xml { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location ^~ /sitemap- { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /rss.xml { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /favicon.svg { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location = /favicon.ico { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
location ~ "^/[0-9a-f]{32}\.txt$" { proxy_pass http://$site; include /data/nginx/custom/chamedoon-site-proxy.inc; }
