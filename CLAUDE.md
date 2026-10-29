# smpp-gateway — Gateway SMPP da TIP Brasil

Projeto do Raphael Pavão. Responder sempre em português.
Repositório: https://github.com/raphaelpavao/SMPP_SERVER (branch `main`).

## Objetivo

Plataforma SMPP gerenciável para uma operação pequena: clientes se conectam via SMPP,
o gateway repassa ao SMSC do fornecedor (já contratado) e devolve DLR e MO.
Interface com login para a equipe operar; futuramente portal do cliente e faturamento no Easy.

**Produto:** DIDs móveis fornecidos pela Surf, sem SIM card — voz via SIP, SMS via esta plataforma.
SMS é bidirecional por DID: MT sai com `source_addr` = DID do cliente; MO chega da Surf com
`destination_addr` = DID e deve ir ao cliente dono daquele DID. Logo, MO é tão central quanto MT,
e cada cliente só pode enviar com origem nos próprios DIDs.

**Modelo de negócio:** a TIP vende para **provedores**, que revendem os DIDs aos clientes deles.
A TIP não gerencia o cliente final. Cada provedor tem área logada (enviar escolhendo um DID dele,
ver recebidas/enviadas) e **APIs** para enviar e para obter as mensagens recebidas. A TIP atribui os
DIDs aos provedores. Cuidado LGPD: definir retenção do texto das mensagens.
APIs **só pull** por decisão do usuário: o servidor nunca inicia contato (sem webhook); o provedor
consulta e recebe o conteúdo. A API é uma aplicação própria (não a HTTP API do Jasmin), que valida
o dono do DID; o Jasmin entrega todo MO a ela, e o mapa DID → provedor fica no banco dela.

## Arquitetura (docker compose)

- **Jasmin** `jookies/jasmin:0.11.0` (+ Redis + RabbitMQ). SMPP clientes na 2775; jcli (8990),
  HTTP API (1401) e painel RabbitMQ (15672, guest/guest) só em 127.0.0.1.
  Entrypoint sobrescrito para ligar `publish_submit_sm_resp = True` no `jasmin.cfg`.
- **Perfil `dev`**: `smsc-simulador` (Python, `simulador/`) — aceita bind, responde submit_sm,
  manda DLR em 1 s e devolve MO de eco para destinos com prefixo `5500`.
- **Perfil `painel`**: Jasmin Web Panel `tarekaec/jasmin_web_panel:3.1.0` em http://localhost:8999
  (`painel`, `painel-celery`, `painel-db` PostgreSQL, `painel-sms-logger`).
  `painel/sms_logger.py` é cópia do upstream (MIT) — não editar, atualizar do original.
  O logger depende do painel estar healthy (senão cria `submit_log` antes das migrations e o painel quebra).
  Redis do painel usa db 1 (Jasmin usa o 0).
- **Perfil `portal`**: portal TIP (código nosso em `portal/`, Django 5.2 LTS + django-unfold 0.110)
  em http://localhost:8000, com `portal-db` PostgreSQL próprio. App `nucleo`: Provedor, DID (E.164,
  sem provedor = estoque), HistoricoDID, Usuario (sem provedor = equipe TIP; com provedor = só
  consulta os dados dele, sem histórico). Importação em lote (lista/CSV/faixas) em `nucleo/servicos.py`.
  Admin criado no 1º start a partir de `PORTAL_ADMIN_USER/PASSWORD` do `.env`.
  Mensagens (`Mensagem`, proxies Enviada/Recebida): envio pela API HTTP do Jasmin com usuário `portal`
  (`nucleo/jasmin.py`, GSM-7 ou UCS-2, dlr-level 3); DLR e MO voltam em `/interno/jasmin/<token>/dlr|mo/`
  (`nucleo/views.py`, respondem `ACK/Jasmin`). No `gateway.json`: cliente `portal`, `conectores_http` e
  `mo_padrao_http` (todo MO sem cliente SMPP vai ao portal, que acha o dono pelo DID). Senha/token ficam
  no `.env` e entram no JSON como `${VAR}` (provisionar expande).
  Unfold não tem pt-BR: traduções extras em `portal/locale/` (.mo gerado no build por `compilar.py`).
  CSS do Unfold só tem as classes que ele usa → layout próprio em style inline.
  Testes: `docker compose --profile portal run --rm --no-deps portal python manage.py test nucleo`
  (rodar no Postgres; SQLite esconde erros, ex.: select_for_update com join). CI tem job `portal`.
- **Perfil `tools`**: `provisionar` aplica `config/gateway.json` via jcli (recria tudo + persist).
  **Não usar onde o painel é a fonte da verdade** — apaga o que foi feito pelo painel.
- `.env` e `config/gateway.json` têm senhas e ficam fora do git (`.env.example`, `gateway.example.json`).

Subir local: `docker compose --profile dev --profile painel --profile portal up -d`.
Teste: `python ferramentas/cliente_teste.py --usuario cliente1 --senha cli123 --destino 5500999990001`.

## Aprendizados técnicos

- jcli é telnet com twisted/recvline: o prompt `Username:` só aparece após negociação NAWS;
  o script manda o usuário logo após "Authentication required.". Remover IAC e ANSI da saída.
- `jasmind -u/-p` só carrega o perfil; o login do jcli vem de `[jcli] admin_username/admin_password` (md5 hex)
  no `jasmin.cfg` — o entrypoint do compose grava a partir do `.env`. Senha de usuário SMPP: máx. 8 caracteres.
- Jasmin traduz o message_id do fornecedor para um UUID próprio; DLR chega ao cliente com o UUID.
- Jasmin 0.11 por HTTP: URL de `httpccm` exige ponto no host (por isso o alias `portal.interno`);
  MO chega com `coding` como byte cru (`chr(8)`), use `binary` (hex) para decodificar; DLR/MO só são
  dados como entregues com corpo `ACK/Jasmin` (senão 3 tentativas a cada 30 s). Mensagem enviada logo
  após o Jasmin reiniciar pode ser descartada sem DLR (fica "na fila").
- RabbitMQ ≥3.8 fecha o canal de consumidor que segura mensagem sem ack por 30 min (`consumer_timeout`);
  o Jasmin não se recupera (filas com 0 consumers, HTTP /send responde `Success "[Failure instance...]"`).
  Compose desliga o limite (`RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS`). Diagnóstico: `rabbitmqctl list_queues name consumers`.
- CI (GitHub Actions) sobe Jasmin + simulador, provisiona e confere DLR e MO. Logs do Actions exigem
  login, mas as anotações não: em falha, o CI joga o diagnóstico nas anotações
  (ler via `api.github.com/repos/raphaelpavao/SMPP_SERVER/check-runs/<job>/annotations`).
- Máquina do usuário: Windows 11, Docker Desktop + WSL2 (instalado em out/2026). Sem `gh` CLI.

## Situação (23/10/2026)

- [x] Jasmin + simulador funcionando local e no CI (envio, DLR, MO, UCS-2, concatenação).
- [x] Painel web no ar local; telas conferidas (SMPPCCM, Users, Submit Logs com export CSV/Excel).
- [ ] Usuário trocar senha padrão do painel (`admin`/`secret`).
- [ ] Repositório está PÚBLICO — recomendado tornar privado.
- [ ] Personalizar o painel. Plano acordado: níveis 1 (marca: nome, logo, cores) e 2 (português,
      esconder menus) com templates/static sobrepostos à imagem oficial via volume, sem fork.
      Nível 3 (portal do cliente, faturamento Easy) como aplicação separada lendo `submit_log`/API do painel.
- [ ] Configurar SMSC real no `gateway.json`/painel (host, porta, system_id, bind, TON/NPI, throughput,
      VPN/TLS) — senha nunca no chat. IP do servidor precisa estar liberado no SMSC.
- [x] Deploy no GCP (29/10/2026): projeto `gateway-smpp`, VM `smpp-gw` (e2-medium, `southamerica-east1-a`,
      Ubuntu 24.04), IP fixo `34.39.247.250` (`smpp-ip`). Projeto em `/opt/smpp-gateway`, instalado por
      `deploy/instalar_vm.sh` (gera `.env` com senhas aleatórias só na VM). Firewall GCP: `vpn-surf`
      (UDP 500/4500 + ESP dos IPs da Surf), `smpp-clientes` (TCP 2775). Teste com simulador OK (envio, DLR, MO).
      gcloud no Windows: `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd`; comandos remotos
      vão melhor como script (`gcloud compute scp` + `ssh --command="sudo bash /tmp/x.sh"`) — aspas quebram no plink.
- [ ] Na VM: trocar senha do painel, RabbitMQ guest; restringir SSH; backup (snapshots); remover simulador e
      `cliente1` de teste quando a Surf estiver configurada.
- [ ] Fornecedor Surf: VPN IPsec (strongSwan na VM) + bind TRX em 170.244.199.43/.55, até 4 binds,
      TON/NPI 1/1, enquire_link 60 s. Formulário "VPN and SMPP - Padrao.docx" a preencher com o IP acima.
- [ ] Decidir fonte da verdade da configuração (painel × gateway.json) para produção.

## Como o usuário gosta de trabalhar

- Explicações objetivas, em tabelas quando ajudar; uma decisão por vez.
- Testar de verdade antes de dizer que funciona (browser, cliente de teste, CI).
- Ações externas (push, publicar, mudar visibilidade) só com o ok dele; senhas/tokens ele mesmo digita.
