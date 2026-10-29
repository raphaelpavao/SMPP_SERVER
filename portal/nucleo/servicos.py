"""Regras de cadastro de DIDs: importação em lote e troca de provedor."""

import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from django.conf import settings
from django.urls import reverse

from . import jasmin
from .models import DID, HistoricoDID, Mensagem
from .validadores import PADRAO_DID, normalizar_did

# Uma faixa maior que isso quase sempre é erro de digitação.
LIMITE_FAIXA = 10_000

_SEPARADORES = re.compile(r"[\r\n,;\t]+")


@dataclass
class ResultadoImportacao:
    criados: list = field(default_factory=list)
    atribuidos_do_estoque: list = field(default_factory=list)
    ja_cadastrados: list = field(default_factory=list)
    de_outro_provedor: list = field(default_factory=list)  # (número, provedor)
    invalidos: list = field(default_factory=list)  # (texto original, motivo)
    repetidos: list = field(default_factory=list)

    @property
    def total_gravado(self):
        return len(self.criados) + len(self.atribuidos_do_estoque)

    @property
    def total_recusado(self):
        return len(self.de_outro_provedor) + len(self.invalidos)


def _expandir_faixa(texto):
    """'5511999990000 - 5511999990099' vira a lista de números; None se não for faixa.

    Só é faixa se os dois lados forem DIDs completos, assim "11 99999-0001" continua
    sendo um número só.
    """
    partes = texto.split("-")
    if len(partes) != 2:
        return None
    inicio, fim = normalizar_did(partes[0]), normalizar_did(partes[1])
    if not inicio or not fim:
        return None
    if int(fim) < int(inicio):
        raise ValueError("faixa invertida (o fim é menor que o início)")
    if int(fim) - int(inicio) + 1 > LIMITE_FAIXA:
        raise ValueError(f"faixa com mais de {LIMITE_FAIXA:_} números".replace("_", "."))
    return [str(n) for n in range(int(inicio), int(fim) + 1)]


def interpretar_lista(texto, resultado):
    """Extrai os DIDs do texto colado ou do CSV, na ordem, sem repetição."""
    numeros, vistos = [], set()
    for item in _SEPARADORES.split(texto or ""):
        item = item.strip().strip('"')
        if not re.search(r"\d", item):
            continue  # linha vazia ou cabeçalho de CSV
        try:
            faixa = _expandir_faixa(item)
        except ValueError as erro:
            resultado.invalidos.append((item, str(erro)))
            continue
        candidatos = faixa if faixa is not None else [normalizar_did(item)]
        for numero in candidatos:
            if not numero or not PADRAO_DID.fullmatch(numero):
                resultado.invalidos.append((item, "não é um móvel brasileiro válido"))
            elif numero in vistos:
                resultado.repetidos.append(numero)
            else:
                vistos.add(numero)
                numeros.append(numero)
    return numeros


def importar_dids(texto, provedor, usuario):
    """Cadastra os DIDs do texto; com provedor, atribui a ele os novos e os que estão em estoque.

    DIDs que já pertencem a outro provedor não são mexidos: transferência é feita
    um a um, de propósito.
    """
    resultado = ResultadoImportacao()
    numeros = interpretar_lista(texto, resultado)
    agora = timezone.now()

    with transaction.atomic():
        existentes = {}
        for i in range(0, len(numeros), 1000):
            lote = numeros[i:i + 1000]
            for did in DID.objects.select_for_update(of=("self",)).select_related("provedor").filter(numero__in=lote):
                existentes[did.numero] = did

        novos, historico = [], []
        for numero in numeros:
            did = existentes.get(numero)
            if did is None:
                novos.append(DID(numero=numero, provedor=provedor, atribuido_em=agora if provedor else None))
            elif did.provedor_id == (provedor.pk if provedor else None):
                resultado.ja_cadastrados.append(numero)
            elif did.provedor_id is None:
                did.provedor, did.atribuido_em = provedor, agora
                did.save(update_fields=["provedor", "atribuido_em"])
                historico.append(HistoricoDID(
                    did=did, evento=HistoricoDID.Evento.IMPORTACAO,
                    provedor_novo=provedor, usuario=usuario,
                ))
                resultado.atribuidos_do_estoque.append(numero)
            else:
                resultado.de_outro_provedor.append((numero, did.provedor))

        for did in DID.objects.bulk_create(novos, batch_size=1000):
            historico.append(HistoricoDID(
                did=did, evento=HistoricoDID.Evento.IMPORTACAO,
                provedor_novo=provedor, usuario=usuario,
            ))
            resultado.criados.append(did.numero)
        HistoricoDID.objects.bulk_create(historico, batch_size=1000)

    return resultado


def registrar_cadastro_ou_troca(did, provedor_anterior, criado, usuario):
    """Chamado após salvar um DID pela tela: grava histórico e data de atribuição."""
    if criado:
        evento = HistoricoDID.Evento.CADASTRO
    elif did.provedor_id != (provedor_anterior.pk if provedor_anterior else None):
        evento = HistoricoDID.Evento.TRANSFERENCIA
    else:
        return
    did.atribuido_em = timezone.now() if did.provedor_id else None
    did.save(update_fields=["atribuido_em"])
    HistoricoDID.objects.create(
        did=did, evento=evento, provedor_anterior=provedor_anterior,
        provedor_novo=did.provedor, usuario=usuario,
    )


# --- Mensagens ---------------------------------------------------------------


class ErroEnvio(Exception):
    pass


def url_interna(nome):
    """URL que o Jasmin chama de volta (rede interna do compose), com o token na rota."""
    caminho = reverse(nome, kwargs={"token": settings.PORTAL_INTERNO_TOKEN})
    return settings.PORTAL_URL_INTERNA.rstrip("/") + caminho


def enviar_sms(did, destino, texto, usuario):
    """Grava a mensagem e entrega ao Jasmin; a falha do gateway fica registrada na mensagem."""
    if not did.ativo:
        raise ErroEnvio("Este DID está inativo.")
    if did.provedor_id is None:
        raise ErroEnvio("Este DID está em estoque (sem provedor).")
    if not did.provedor.ativo:
        raise ErroEnvio("O provedor deste DID está suspenso.")

    msg = Mensagem.objects.create(
        direcao=Mensagem.Direcao.MT, provedor=did.provedor, did=did,
        numero_remoto=destino, texto=texto, status=Mensagem.Status.PENDENTE, usuario=usuario,
    )
    try:
        msg.id_jasmin = jasmin.enviar(did.numero, destino, texto, url_interna("interno_dlr"))
        msg.status = Mensagem.Status.NA_FILA
    except jasmin.ErroJasmin as erro:
        msg.status, msg.status_detalhe = Mensagem.Status.FALHOU, str(erro)[:200]
    msg.save(update_fields=["id_jasmin", "status", "status_detalhe", "atualizada_em"])
    return msg


# Status de DLR (nível 2, vindo da operadora) que indicam falha definitiva.
_DLR_FALHA = {"UNDELIV", "REJECTD", "EXPIRED", "DELETED", "UNKNOWN"}


def registrar_dlr(id_jasmin, nivel, status_dlr, erro=""):
    """Atualiza a mensagem a partir do DLR. Devolve False se a mensagem não existe (ainda)."""
    with transaction.atomic():
        msg = Mensagem.objects.select_for_update().filter(id_jasmin=id_jasmin).first()
        if msg is None:
            return False
        if msg.status in Mensagem.FINAIS:
            return True
        if str(nivel) == "1":
            novo = Mensagem.Status.ACEITA if status_dlr == "ESME_ROK" else Mensagem.Status.FALHOU
        elif status_dlr == "DELIVRD":
            novo = Mensagem.Status.ENTREGUE
        elif status_dlr in _DLR_FALHA:
            novo = Mensagem.Status.FALHOU
        else:
            novo = Mensagem.Status.ACEITA  # ENROUTE, ACCEPTD...
        msg.status = novo
        msg.status_detalhe = status_dlr + (f" (erro {erro})" if erro and erro != "000" else "")
        msg.save(update_fields=["status", "status_detalhe", "atualizada_em"])
    return True


def registrar_mo(id_jasmin, remetente, destino, texto):
    """Grava o SMS recebido no DID de destino. Devolve a mensagem ou None se o DID não for nosso."""
    numero = normalizar_did(destino)
    did = DID.objects.select_related("provedor").filter(numero=numero).first() if numero else None
    if did is None or did.provedor_id is None:
        return None
    msg, _ = Mensagem.objects.get_or_create(
        id_jasmin=f"mo:{id_jasmin}",  # o Jasmin reenvia se não receber o ACK; não duplicar
        defaults=dict(
            direcao=Mensagem.Direcao.MO, provedor=did.provedor, did=did,
            # PostgreSQL não aceita NUL em texto; um MO mal codificado não pode derrubar o recebimento.
            numero_remoto=remetente[:32], texto=texto.replace(chr(0), ""), status=Mensagem.Status.RECEBIDA,
        ),
    )
    return msg
