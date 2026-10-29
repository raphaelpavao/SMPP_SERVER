"""Endpoints internos chamados pelo Jasmin (DLR e MO).

Só o Jasmin conhece a URL: o token vai na rota e é conferido aqui. O Jasmin
considera entregue apenas quando recebe o corpo "ACK/Jasmin"; qualquer outra
resposta faz ele tentar de novo (até 3 vezes, a cada 30 s).
"""

import logging
import secrets

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .servicos import registrar_dlr, registrar_mo

log = logging.getLogger(__name__)

ACK = "ACK/Jasmin"


def _conferir_token(token):
    if not settings.PORTAL_INTERNO_TOKEN or not secrets.compare_digest(token, settings.PORTAL_INTERNO_TOKEN):
        raise Http404


def decodificar_mo(dados):
    """Texto do MO a partir do 'binary' (hex) e do 'coding' informados pelo Jasmin."""
    try:
        bruto = bytes.fromhex(dados.get("binary", ""))
    except ValueError:
        return dados.get("content", "")
    coding = dados.get("coding", "0")
    if len(coding) == 1 and not coding.isdigit():
        coding = str(ord(coding))  # o Jasmin 0.11 manda o byte cru (chr(8)), não o texto "8"
    if coding == "8":
        return bruto.decode("utf-16-be", errors="replace")
    if coding == "3":
        return bruto.decode("latin-1")
    # coding 0 (alfabeto padrão): na prática chega ASCII; se não, tenta UTF-8 e cai em Latin-1.
    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError:
        return bruto.decode("latin-1")


@csrf_exempt
@require_POST
def dlr(request, token):
    _conferir_token(token)
    d = request.POST
    if not registrar_dlr(d.get("id", ""), d.get("level", ""), d.get("message_status", ""), d.get("err", "")):
        # O DLR pode chegar antes de gravarmos o ID do envio: sem ACK, o Jasmin tenta de novo.
        log.warning("DLR para mensagem desconhecida: %s", d.get("id"))
        return HttpResponse("mensagem desconhecida", status=404)
    return HttpResponse(ACK)


@csrf_exempt
@require_POST
def mo(request, token):
    _conferir_token(token)
    d = request.POST
    log.info("MO %s: %s -> %s (coding %r)", d.get("id"), d.get("from"), d.get("to"), d.get("coding"))
    msg = registrar_mo(d.get("id", ""), d.get("from", ""), d.get("to", ""), decodificar_mo(d))
    if msg is None:
        # DID desconhecido ou em estoque: confirma assim mesmo para o Jasmin não insistir.
        log.warning("MO para número sem dono: %s -> %s", d.get("from"), d.get("to"))
    return HttpResponse(ACK)
