"""Cliente da API HTTP do Jasmin (porta 1401) usada para enviar SMS."""

import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

# Alfabeto GSM 03.38 (básico + extensão). Fora dele a mensagem vai em UCS-2.
_GSM_BASICO = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXTENSAO = "^{}\\[~]|€\f"
_GSM = set(_GSM_BASICO + _GSM_EXTENSAO)


class ErroJasmin(Exception):
    pass


def e_gsm7(texto):
    return all(c in _GSM for c in texto)


def partes_sms(texto):
    """Quantos SMS a mensagem ocupa (concatenação cobra por parte)."""
    if e_gsm7(texto):
        tamanho = sum(2 if c in _GSM_EXTENSAO else 1 for c in texto)
        return 1 if tamanho <= 160 else -(-tamanho // 153)
    tamanho = len(texto.encode("utf-16-be")) // 2
    return 1 if tamanho <= 70 else -(-tamanho // 67)


def enviar(origem, destino, texto, url_dlr):
    """Entrega a mensagem ao Jasmin e devolve o ID que ele atribuiu."""
    campos = {
        "username": settings.JASMIN_USUARIO,
        "password": settings.JASMIN_SENHA,
        "from": origem,
        "to": destino,
        "dlr": "yes",
        "dlr-url": url_dlr,
        "dlr-level": "3",  # 1 = aceite do fornecedor, 2 = entrega no aparelho
        "dlr-method": "POST",
    }
    if e_gsm7(texto):
        campos.update(coding="0", content=texto)
    else:
        campos.update(coding="8", **{"hex-content": texto.encode("utf-16-be").hex()})

    requisicao = urllib.request.Request(
        settings.JASMIN_HTTP_URL.rstrip("/") + "/send",
        data=urllib.parse.urlencode(campos).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=10) as resposta:
            corpo = resposta.read().decode(errors="replace")
    except urllib.error.HTTPError as erro:
        corpo = erro.read().decode(errors="replace")
        raise ErroJasmin(f"gateway recusou ({erro.code}): {corpo.strip()[:150]}") from erro
    except (urllib.error.URLError, TimeoutError) as erro:
        raise ErroJasmin(f"gateway indisponível: {erro}") from erro

    achado = re.match(r'Success "([^"]+)"', corpo.strip())
    if not achado:
        raise ErroJasmin(f"resposta inesperada do gateway: {corpo.strip()[:150]}")
    return achado.group(1)
