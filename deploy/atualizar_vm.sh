#!/usr/bin/env bash
# Aplica na VM um pacote com o código novo (gerado na máquina de desenvolvimento):
#
#   tar -czf smpp-gateway.tgz $(git ls-files)
#   gcloud compute scp smpp-gateway.tgz deploy/atualizar_vm.sh smpp-gw:/tmp/
#   gcloud compute ssh smpp-gw --command="sudo PERFIS='dev painel portal' bash /tmp/atualizar_vm.sh"
#
# Preserva .env e config/gateway.json (fora do pacote). Acrescenta ao gateway.json
# o que o portal precisa no Jasmin e reaplica a configuração com o provisionar.
set -euo pipefail

DESTINO=/opt/smpp-gateway
PACOTE=${PACOTE:-/tmp/smpp-gateway.tgz}
PERFIS="${PERFIS:-painel portal}"

cd "$DESTINO"
tar -xzf "$PACOTE" -C "$DESTINO"
echo "Código atualizado a partir de $PACOTE"

# gateway.json: acrescenta o usuário, o conector HTTP e a rota MO do portal (do exemplo).
if [[ " $PERFIS " == *" portal "* ]] && [ -f config/gateway.json ]; then
  python3 - <<'PY'
import json
caminho = "config/gateway.json"
cfg = json.load(open(caminho, encoding="utf-8"))
exemplo = json.load(open("config/gateway.example.json", encoding="utf-8"))
mudou = False
if not any(c["uid"] == "portal" for c in cfg.get("clientes", [])):
    cfg.setdefault("clientes", []).append(next(c for c in exemplo["clientes"] if c["uid"] == "portal"))
    mudou = True
for chave in ("conectores_http", "mo_padrao_http"):
    if chave not in cfg:
        cfg[chave] = exemplo[chave]
        mudou = True
if mudou:
    json.dump(cfg, open(caminho, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("gateway.json: partes do portal acrescentadas")
PY
fi

PERFIS="$PERFIS" bash deploy/instalar_vm.sh

# Se o RabbitMQ foi recriado, o Jasmin e o logger precisam reconectar.
docker compose restart jasmin
docker compose --profile painel restart painel-sms-logger 2>/dev/null || true
sleep 15

docker compose run --rm -T provisionar
