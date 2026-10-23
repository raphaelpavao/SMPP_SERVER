# smpp-gateway

Gateway SMPP da TIP Brasil baseado no [Jasmin](https://jasminsms.com).
Os clientes se conectam aqui via SMPP; o gateway repassa as mensagens (MT) ao
SMSC do fornecedor e devolve confirmações de entrega (DLR) e mensagens
recebidas (MO) para o cliente certo.

```
Clientes (ESME) ──SMPP:2775──▶ Jasmin ──SMPP──▶ SMSC do fornecedor
                ◀── DLR / MO ──  │   ◀── DLR / MO ──
                                 ├── Redis     (DLRs, mapeamento de IDs)
                                 └── RabbitMQ  (filas de envio/recebimento)
```

## Estrutura

| Caminho | O que é |
|---|---|
| `docker-compose.yml` | Jasmin + Redis + RabbitMQ (+ simulador e provisionamento como perfis) |
| `.env.example` | Portas, versão e senha do jcli. Copiar para `.env` |
| `config/gateway.example.json` | Fornecedores, rotas e clientes. Copiar para `config/gateway.json` |
| `provisionamento/provisionar.py` | Aplica o `gateway.json` no Jasmin (via jcli) e faz `persist` |
| `simulador/` | SMSC falso para testes: responde, manda DLR e faz eco de MO |
| `ferramentas/cliente_teste.py` | Cliente SMPP de linha de comando para testar envio/DLR/MO |

`.env` e `config/gateway.json` têm senhas e **não vão para o git**.

## Rodando na máquina (desenvolvimento)

Pré-requisito: Docker (no Windows, Docker Desktop com WSL 2).

```bash
cp .env.example .env
cp config/gateway.example.json config/gateway.json   # já aponta para o simulador
docker compose --profile dev up -d                   # Jasmin + Redis + RabbitMQ + simulador
docker compose run --rm provisionar                  # cria fornecedor, rotas e cliente1
```

Teste ponta a ponta (cliente1 → Jasmin → simulador → DLR/MO de volta):

```bash
pip install -r ferramentas/requirements.txt
python ferramentas/cliente_teste.py --usuario cliente1 --senha cli123 --destino 5500999990001 --texto "Olá"
```

Saída esperada: `submit_sm_resp`, depois o `DLR` (stat:DELIVRD) e o `MO` "ECO: Olá"
(destinos que começam com `5500` voltam como MO no simulador).

Logs: `docker compose logs -f jasmin smsc-simulador`.

## Administração (jcli)

O jcli fica só em `127.0.0.1:8990` (usuário/senha do `.env`). Num servidor Linux:
`telnet 127.0.0.1 8990`. Comandos úteis:

```
smppccm -l          # fornecedores e estado do bind (BOUND_TRX = conectado)
user -l             # clientes
mtrouter -l         # rotas de envio
morouter -l         # rotas de recebimento
stats --smppcs      # estatísticas dos conectores
```

A fonte da verdade é o `config/gateway.json`: altere o JSON e rode o
provisionamento de novo em vez de mudar à mão no jcli (o script recria tudo).

## Deploy (servidor Linux)

1. Instalar Docker Engine + plugin compose.
2. `git clone` deste repositório.
3. Criar `.env` (trocar `JCLI_PASSWORD`) e `config/gateway.json` com os dados reais do SMSC.
4. `docker compose up -d` (sem `--profile dev`, ou seja, sem simulador).
5. `docker compose run --rm provisionar`.
6. Firewall: liberar a porta SMPP (2775) **só** para os IPs dos clientes; o IP do
   servidor precisa estar liberado no SMSC do fornecedor.

Atualizar: `git pull && docker compose pull && docker compose up -d`.
A configuração do Jasmin fica no volume `jasmin-store` e sobrevive a reinícios.

## Próximos passos

- [ ] Dados reais do SMSC no `gateway.json` (TON/NPI, throughput, bind, VPN/TLS)
- [ ] Bilhetagem: exportar MTs por cliente/mês para faturar no Easy
- [ ] TLS para os clientes (stunnel na frente da porta 2775) ou VPN
- [ ] Monitoramento (Prometheus/Grafana — o Jasmin já expõe métricas)
- [ ] Backup do volume `jasmin-store`
