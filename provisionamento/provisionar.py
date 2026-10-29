"""Aplica a configuração de config/gateway.json no Jasmin via jcli.

A configuração do Jasmin vive no próprio Jasmin (jcli + persist). Este script
torna isso reproduzível: o JSON é a fonte da verdade e o script recria
fornecedores, rotas, grupos e usuários a partir dele, depois faz persist.

Uso:
    python provisionar.py config/gateway.json

Variáveis: JCLI_HOST (127.0.0.1), JCLI_PORT (8990), JCLI_USER, JCLI_PASSWORD.
Textos do JSON aceitam ${VARIAVEL} do ambiente (ex.: senha e token do portal),
para que segredos fiquem só no .env.

Atenção: usuários são removidos e recriados, então saldos/quotas definidos
manualmente no jcli se perdem. Defina tudo no JSON.
"""
import json
import os
import re
import socket
import sys
import time

PROMPT_PRINCIPAL = "jcli : "
PROMPT_SESSAO = "> "
DEBUG = os.environ.get("PROVISIONAR_DEBUG") == "1"
PALAVRAS_DE_ERRO =("Unknown", "Error", "error", "Invalid", "must ", "Cannot", "not found", "Incorrect")

# Chaves do fornecedor no JSON que vão direto para o smppccm
CHAVES_SMPPCCM = (
    "host", "port", "username", "password", "systype", "bind", "ssl", "submit_throughput",
    "src_ton", "src_npi", "dst_ton", "dst_npi", "bind_ton", "bind_npi", "src_addr",
    "elink_interval", "dlr_msgid", "dlr_expiry", "con_loss_retry", "con_fail_retry",
    "con_loss_delay", "con_fail_delay", "coding", "loglevel",
)


class JCli:
    def __init__(self, host, port, usuario, senha, timeout=15):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buffer = b""
        # O jcli só desenha o prompt "Username: " depois da negociação NAWS
        # (tamanho de janela), que não fazemos; mas ele já aceita o usuário.
        self._ler_ate(("Authentication required.", "Username: "))
        self._enviar(usuario)
        self._ler_ate(("Password: ",))
        self._enviar(senha)
        saida = self._ler_ate((PROMPT_PRINCIPAL, "Username: "))
        if not saida.endswith(PROMPT_PRINCIPAL):
            raise RuntimeError("Falha no login do jcli (usuário/senha)")

    def _enviar(self, linha):
        self.sock.sendall(linha.encode() + b"\r\n")

    def _ler_ate(self, prompts):
        while True:
            texto = self._limpo()
            for p in prompts:
                if texto.rstrip(" \n").endswith(p.rstrip(" ")):
                    self.buffer = b""
                    return texto
            try:
                pedaco = self.sock.recv(4096)
            except socket.timeout:
                raise RuntimeError(f"jcli não mostrou {prompts}. Bytes recebidos: {self.buffer!r}") from None
            if not pedaco:
                raise RuntimeError("Conexão com o jcli encerrada. Recebido até agora:\n" + texto)
            self.buffer += pedaco

    def _limpo(self):
        # Remove negociação telnet (IAC ...), sequências ANSI e normaliza quebras de linha
        dados = re.sub(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]", b"", self.buffer)
        dados = re.sub(rb"\x1b(\[[0-9;?]*[A-Za-z]|[A-Za-z])", b"", dados)
        return dados.decode(errors="replace").replace("\r", "")

    def cmd(self, linha, tolerar_erro=False):
        """Envia um comando e devolve a saída (sem o prompt)."""
        self._enviar(linha)
        saida = self._ler_ate((PROMPT_PRINCIPAL, PROMPT_SESSAO))
        corpo = saida.rsplit("\n", 1)[0] if "\n" in saida else ""
        if DEBUG:
            print(f"[jcli] {corpo.strip()}")
        if not tolerar_erro and any(p in corpo for p in PALAVRAS_DE_ERRO):
            raise RuntimeError(f"jcli recusou '{linha}':\n{corpo}")
        return corpo

    def sessao(self, abrir, pares):
        """Abre uma sessão de criação (ex.: 'user -a'), define chave/valor e confirma com ok."""
        self.cmd(abrir)
        try:
            for chave, valor in pares:
                self.cmd(f"{chave} {valor}")
            return self.cmd("ok")
        except RuntimeError:
            self.cmd("ko", tolerar_erro=True)
            raise

    def fechar(self):
        try:
            self._enviar("quit")
        finally:
            self.sock.close()


def expandir_variaveis(valor):
    """Troca ${VAR} pelo valor do ambiente em todos os textos do JSON."""
    if isinstance(valor, str):
        expandido = os.path.expandvars(valor)
        if "${" in expandido:
            raise SystemExit(f"Variável de ambiente não definida em: {valor}")
        return expandido
    if isinstance(valor, list):
        return [expandir_variaveis(v) for v in valor]
    if isinstance(valor, dict):
        return {k: expandir_variaveis(v) for k, v in valor.items()}
    return valor


def ids_existentes(jcli, comando):
    """Lista os ids (primeira coluna, prefixada por #) de um comando -l."""
    saida = jcli.cmd(comando, tolerar_erro=True)
    ids = []
    for linha in saida.splitlines():
        m = re.match(r"#(\S+)", linha.strip())
        if m and not m.group(1).lower().startswith(("connector", "user", "group", "filter", "order", "httpcc")):
            ids.append(m.group(1))
    return ids


def provisionar(jcli, cfg):
    # 1. Limpa o que existe (rotas antes de conectores/usuários que elas referenciam)
    print("Limpando rotas, filtros, usuários, grupos e conectores...")
    jcli.cmd("mtrouter -f", tolerar_erro=True)
    jcli.cmd("morouter -f", tolerar_erro=True)
    for fid in ids_existentes(jcli, "filter -l"):
        jcli.cmd(f"filter -r {fid}", tolerar_erro=True)
    for uid in ids_existentes(jcli, "user -l"):
        jcli.cmd(f"user -r {uid}", tolerar_erro=True)
    for gid in ids_existentes(jcli, "group -l"):
        jcli.cmd(f"group -r {gid}", tolerar_erro=True)
    for cid in ids_existentes(jcli, "smppccm -l"):
        jcli.cmd(f"smppccm -0 {cid}", tolerar_erro=True)
        jcli.cmd(f"smppccm -r {cid}", tolerar_erro=True)
    for cid in ids_existentes(jcli, "httpccm -l"):
        jcli.cmd(f"httpccm -r {cid}", tolerar_erro=True)

    # 2. Fornecedores (conectores SMPP cliente)
    for f in cfg["fornecedores"]:
        pares = [("cid", f["cid"])]
        pares += [(k, f[k]) for k in CHAVES_SMPPCCM if f.get(k) not in (None, "")]
        jcli.sessao("smppccm -a", pares)
        print(f"Fornecedor {f['cid']} criado ({f['host']}:{f['port']})")

    # 3. Rota MT padrão -> fornecedor
    jcli.sessao("mtrouter -a", [
        ("type", "DefaultRoute"),
        ("connector", f"smppc({cfg['rota_mt_padrao']})"),
        ("rate", "0.00"),
    ])
    print(f"Rota MT padrão -> {cfg['rota_mt_padrao']}")

    # 4. Grupos e clientes
    for gid in cfg.get("grupos", []):
        jcli.sessao("group -a", [("gid", gid)])
    for c in cfg.get("clientes", []):
        jcli.sessao("user -a", [
            ("uid", c["uid"]),
            ("gid", c["gid"]),
            ("username", c["username"]),
            ("password", c["password"]),
        ])
        print(f"Cliente {c['uid']} criado")

    # 5. Conectores HTTP (para onde o Jasmin entrega MO por HTTP, ex.: o portal)
    for h in cfg.get("conectores_http", []):
        jcli.sessao("httpccm -a", [("cid", h["cid"]), ("url", h["url"]), ("method", h.get("method", "POST"))])
        print(f"Conector HTTP {h['cid']} criado")

    # 6. Rotas MO: por número do cliente e uma padrão
    ordem = 10
    for c in cfg.get("clientes", []):
        for i, regex in enumerate(c.get("mo_destinos", []), 1):
            fid = f"mo_{c['uid']}_{i}"
            jcli.sessao("filter -a", [
                ("fid", fid),
                ("type", "DestinationAddrFilter"),
                ("destination_addr", regex),
            ])
            jcli.sessao("morouter -a", [
                ("type", "StaticMORoute"),
                ("order", ordem),
                ("filters", fid),
                ("connector", f"smpps({c['uid']})"),
            ])
            print(f"Rota MO {regex} -> {c['uid']}")
            ordem += 10
    if cfg.get("mo_padrao_http"):
        # Todo MO que não casou com um cliente SMPP vai para o conector HTTP (o portal
        # descobre o dono pelo DID de destino).
        jcli.sessao("morouter -a", [
            ("type", "DefaultRoute"),
            ("connector", f"http({cfg['mo_padrao_http']})"),
        ])
        print(f"Rota MO padrão -> http({cfg['mo_padrao_http']})")
    elif cfg.get("cliente_mo_padrao"):
        jcli.sessao("morouter -a", [
            ("type", "DefaultRoute"),
            ("connector", f"smpps({cfg['cliente_mo_padrao']})"),
        ])
        print(f"Rota MO padrão -> {cfg['cliente_mo_padrao']}")

    # 7. Sobe os fornecedores e grava
    for f in cfg["fornecedores"]:
        jcli.cmd(f"smppccm -1 {f['cid']}")
    jcli.cmd("persist")
    print("Configuração aplicada e persistida.")


def main():
    caminho = sys.argv[1] if len(sys.argv) > 1 else "config/gateway.json"
    with open(caminho, encoding="utf-8") as fp:
        cfg = expandir_variaveis(json.load(fp))

    host = os.environ.get("JCLI_HOST", "127.0.0.1")
    port = int(os.environ.get("JCLI_PORT", "8990"))

    # O Jasmin pode levar alguns segundos para abrir o jcli depois de subir
    for tentativa in range(30):
        try:
            jcli = JCli(host, port, os.environ.get("JCLI_USER", "jcliadmin"),
                        os.environ.get("JCLI_PASSWORD", "jclipwd"))
            break
        except ConnectionRefusedError:
            if tentativa == 29:
                raise
            time.sleep(2)

    try:
        provisionar(jcli, cfg)
    finally:
        jcli.fechar()


if __name__ == "__main__":
    main()
