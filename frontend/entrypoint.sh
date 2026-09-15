#!/bin/sh
# Vygeneruje /usr/share/nginx/html/config.js s aktualni API URL.
# Umoznuje nastavit backend URL pres promennou prostredi bez rebuildu image.
set -e

API_URL="${API_BASE_URL:-http://localhost:8091}"

cat > /usr/share/nginx/html/config.js <<EOF
window.EUROMAP63_CONFIG = {
  apiBaseUrl: "${API_URL}",
  machineCode: "${MACHINE_CODE:-KM-MC5-01}"
};
EOF

echo "Frontend config vygenerovan: apiBaseUrl=${API_URL}"
