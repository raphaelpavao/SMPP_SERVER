#!/usr/bin/env bash
# Instala/atualiza o gateway numa VM Ubuntu 24.04 (rodar como root, de dentro
# do diretório do projeto já copiado para /opt/smpp-gateway).
#
#   sudo bash deploy/instalar_vm.sh
#   sudo PERFIS="dev painel portal" bash deploy/instalar_vm.sh   # com o simulador
#
# Idempotente: pode rodar de novo a cada atualização. Senhas que faltam no .env
# são geradas aleatoriamente (ficam só na VM, em /opt/smpp-gateway/.env).
set -euo pipefail

cd "$(dirname "$0")/.."
PERFIS="${PERFIS:-painel portal}"

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null; then
  apt-get update -q
  apt-get install -y -q ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# --- .env com senhas aleatórias ---------------------------------------------
senha() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-24}"; }

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i \
    -e "s|^JCLI_PASSWORD=.*|JCLI_PASSWORD=$(senha)|" \
    -e "s|^PAINEL_SECRET_KEY=.*|PAINEL_SECRET_KEY=$(senha 50)|" \
    -e "s|^PAINEL_DB_PASSWORD=.*|PAINEL_DB_PASSWORD=$(senha)|" \
    .env
  # As variáveis do portal vêm do exemplo com valores fixos: são trocadas abaixo.
  sed -i '/^PORTAL_\(SECRET_KEY\|DB_PASSWORD\|JASMIN_SENHA\|INTERNO_TOKEN\|ADMIN_PASSWORD\)=/d' .env
  chmod 600 .env
  echo ".env criado com senhas aleatórias"
fi

# Acrescenta ao .env o que ainda não existe (instalações anteriores ao portal).
garantir() { grep -q "^$1=" .env || { echo "$1=$2" >> .env; echo "  + $1"; }; }
garantir PORTAL_SECRET_KEY "$(senha 50)"
garantir PORTAL_DB_PASSWORD "$(senha)"
garantir PORTAL_JASMIN_SENHA "$(senha 8)"   # usuário SMPP/HTTP do Jasmin: máx. 8 caracteres
garantir PORTAL_INTERNO_TOKEN "$(senha 32)"
garantir PORTAL_ADMIN_USER admin
garantir PORTAL_ADMIN_PASSWORD "$(senha 16)"

# --- Sobe os serviços -------------------------------------------------------
ARGS=()
for p in $PERFIS; do ARGS+=(--profile "$p"); done
docker compose "${ARGS[@]}" pull -q --ignore-buildable
docker compose "${ARGS[@]}" up -d --build --remove-orphans
docker compose "${ARGS[@]}" ps
