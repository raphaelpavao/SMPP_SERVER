#!/usr/bin/env bash
# Instala/atualiza o gateway numa VM Ubuntu 24.04 (rodar como root, de dentro
# do diretório do projeto já copiado para /opt/smpp-gateway).
#
#   sudo bash deploy/instalar_vm.sh
#
# Idempotente: pode rodar de novo a cada atualização. Na primeira vez gera o
# .env com senhas aleatórias (ficam só na VM, em /opt/smpp-gateway/.env).
set -euo pipefail

cd "$(dirname "$0")/.."

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

# --- .env com senhas aleatórias (só na primeira vez) ------------------------
if [ ! -f .env ]; then
  senha() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-24}"; }
  cp .env.example .env
  sed -i \
    -e "s|^JCLI_PASSWORD=.*|JCLI_PASSWORD=$(senha)|" \
    -e "s|^PAINEL_SECRET_KEY=.*|PAINEL_SECRET_KEY=$(senha 50)|" \
    -e "s|^PAINEL_DB_PASSWORD=.*|PAINEL_DB_PASSWORD=$(senha)|" \
    .env
  chmod 600 .env
  echo ".env criado com senhas aleatórias"
fi

# --- Sobe Jasmin + painel ---------------------------------------------------
docker compose --profile painel pull -q
docker compose --profile painel up -d --build --remove-orphans
docker compose --profile painel ps
