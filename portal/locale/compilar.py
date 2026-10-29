"""Gera os .mo a partir dos .po sem depender do gettext (msgfmt) na imagem.

Suporta o subconjunto usado aqui: msgid/msgstr simples, com continuação em
linhas entre aspas. Uso: python locale/compilar.py
"""

import ast
import struct
from pathlib import Path


def ler_po(caminho):
    entradas, atual, campo = {}, {}, None
    for linha in caminho.read_text(encoding="utf-8").splitlines() + [""]:
        linha = linha.strip()
        if linha.startswith(("msgid ", "msgstr ")):
            campo, valor = linha.split(" ", 1)
            if campo == "msgid" and "msgstr" in atual:
                entradas[atual["msgid"]] = atual["msgstr"]
                atual = {}
            atual[campo] = ast.literal_eval(valor)
        elif linha.startswith('"') and campo:
            atual[campo] += ast.literal_eval(linha)
    if "msgstr" in atual:
        entradas[atual["msgid"]] = atual["msgstr"]
    return entradas


def gravar_mo(entradas, caminho):
    chaves = sorted(entradas)
    ids = [k.encode() for k in chaves]
    strs = [entradas[k].encode() for k in chaves]
    n = len(chaves)
    inicio_tabelas = 7 * 4
    inicio_dados = inicio_tabelas + n * 16
    tabela_ids, tabela_strs, dados = [], [], b""
    for texto in ids:
        tabela_ids.append((len(texto), inicio_dados + len(dados)))
        dados += texto + b"\0"
    for texto in strs:
        tabela_strs.append((len(texto), inicio_dados + len(dados)))
        dados += texto + b"\0"
    cabecalho = struct.pack("<7I", 0x950412DE, 0, n, inicio_tabelas, inicio_tabelas + n * 8, 0, 0)
    corpo = b"".join(struct.pack("<2I", *par) for par in tabela_ids + tabela_strs)
    caminho.write_bytes(cabecalho + corpo + dados)


if __name__ == "__main__":
    for po in Path(__file__).parent.rglob("*.po"):
        gravar_mo(ler_po(po), po.with_suffix(".mo"))
        print(f"{po.with_suffix('.mo')} gerado")
