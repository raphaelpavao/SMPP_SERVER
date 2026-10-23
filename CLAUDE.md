# smpp-gateway — Gateway SMPP da TIP Brasil

Projeto do Raphael Pavão. Responder sempre em português.
Repositório: https://github.com/raphaelpavao/SMPP_SERVER (branch `main`).

## Objetivo

Plataforma SMPP gerenciável para uma operação pequena: clientes se conectam via SMPP,
o gateway repassa ao SMSC do fornecedor (já contratado) e devolve DLR e MO.
Interface com login para a equipe operar; futuramente portal do cliente e faturamento no Easy.

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
- **Perfil `tools`**: `provisionar` aplica `config/gateway.json` via jcli (recria tudo + persist).
  **Não usar onde o painel é a fonte da verdade** — apaga o que foi feito pelo painel.
- `.env` e `config/gateway.json` têm senhas e ficam fora do git (`.env.example`, `gateway.example.json`).

Subir local: `docker compose --profile dev --profile painel up -d`.
Teste: `python ferramentas/cliente_teste.py --usuario cliente1 --senha cli123 --destino 5500999990001`.

## Aprendizados técnicos

- jcli é telnet com twisted/recvline: o prompt `Username:` só aparece após negociação NAWS;
  o script manda o usuário logo após "Authentication required.". Remover IAC e ANSI da saída.
- Jasmin traduz o message_id do fornecedor para um UUID próprio; DLR chega ao cliente com o UUID.
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
- [ ] Deploy em servidor Linux; trocar senhas (jcli, RabbitMQ, painel); firewall na 2775; backup dos volumes.
- [ ] Decidir fonte da verdade da configuração (painel × gateway.json) para produção.

## Como o usuário gosta de trabalhar

- Explicações objetivas, em tabelas quando ajudar; uma decisão por vez.
- Testar de verdade antes de dizer que funciona (browser, cliente de teste, CI).
- Ações externas (push, publicar, mudar visibilidade) só com o ok dele; senhas/tokens ele mesmo digita.
