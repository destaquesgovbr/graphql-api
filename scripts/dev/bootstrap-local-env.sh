#!/usr/bin/env bash
# Gera (ou regenera) `graphql-api/.env.local` puxando secrets do GCP Secret
# Manager. Idempotente — pode rodar sempre que precisar atualizar valores.
#
# Pre-requisitos:
#   - gcloud autenticado em `inspire-7-finep`
#   - Voce com role `roles/secretmanager.secretAccessor` (ou broader) no projeto
#
# Uso:
#   ./scripts/dev/bootstrap-local-env.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"
GCP_PROJECT="${GCP_PROJECT:-inspire-7-finep}"
KC_URL="${KC_URL:-https://destaquesgovbr-keycloak-klvx64dufq-rj.a.run.app}"

echo "→ Lendo secrets do GCP Secret Manager (project=$GCP_PROJECT)..." >&2

fetch() {
  gcloud secrets versions access latest --secret="$1" --project="$GCP_PROJECT"
}

TYPESENSE_READ=$(fetch typesense-read-conn)
TYPESENSE_WRITE=$(fetch typesense-write-conn)
PG_DSN=$(fetch govbrnews-postgres-connection-string)

cat > "$ENV_FILE" <<EOF
# Gerado por scripts/dev/bootstrap-local-env.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ)).
# Gitignored. NUNCA commitar — contem credenciais reais de prod.

# --- GCP / Firestore ---
GCP_PROJECT_ID=$GCP_PROJECT
GOOGLE_CLOUD_PROJECT=$GCP_PROJECT

# --- Typesense (Cloud) ---
TYPESENSE_READ_CONN='$TYPESENSE_READ'
TYPESENSE_WRITE_CONN='$TYPESENSE_WRITE'

# --- Postgres Cloud SQL (govbrnews) ---
DATABASE_URL='$PG_DSN'

# --- Keycloak (validacao JWT inbound) ---
AUTH_JWKS_URL=$KC_URL/realms/destaquesgovbr/protocol/openid-connect/certs
AUTH_ISSUER=$KC_URL/realms/destaquesgovbr

# --- CORS (portal local em :3000) ---
CORS_ALLOW_ORIGINS=http://localhost:3000

# --- Upstream: clipping worker (subscription generateRecortes) ---
# Em prod, Terraform injeta esta URL automaticamente. Local apontamos para o
# mesmo Cloud Run; OIDC pode falhar sem ADC adequado (ver CLAUDE.md).
CLIPPING_WORKER_URL=https://destaquesgovbr-clipping-klvx64dufq-rj.a.run.app/agent/generate-recortes

# --- Logging ---
LOG_LEVEL=INFO
EOF

chmod 600 "$ENV_FILE"
echo "✓ Gravado $ENV_FILE ($(wc -l < "$ENV_FILE") linhas, perms 600)" >&2
