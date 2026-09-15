FROM nginx:1.27-alpine

COPY index.html machine.html hledat.html style.css app.js hala.js hledat.js /usr/share/nginx/html/
COPY vendor/ /usr/share/nginx/html/vendor/
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY entrypoint.sh /docker-entrypoint.d/40-generate-config.sh

RUN chmod +x /docker-entrypoint.d/40-generate-config.sh

EXPOSE 80
