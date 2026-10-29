"""Normalização e validação de números (DID) e CNPJ."""

import re

from django.core.exceptions import ValidationError

# DID móvel brasileiro em E.164 sem "+": 55 + DDD (dois dígitos de 1 a 9) + 9 + 8 dígitos.
PADRAO_DID = re.compile(r"55[1-9]{2}9\d{8}")


def normalizar_did(texto):
    """Devolve o DID em E.164 (5511999990001) ou None se não for um móvel válido.

    Aceita máscara e formatos usuais: "+55 (11) 99999-0001", "11999990001".
    """
    digitos = re.sub(r"\D", "", texto or "")
    if len(digitos) == 11:
        digitos = "55" + digitos
    return digitos if PADRAO_DID.fullmatch(digitos) else None


def validar_did(valor):
    if not PADRAO_DID.fullmatch(valor or ""):
        raise ValidationError(
            "DID inválido. Use 55 + DDD + número móvel com 9 dígitos, sem espaços (ex.: 5511999990001)."
        )


def formatar_did(numero):
    if not PADRAO_DID.fullmatch(numero or ""):
        return numero
    return f"+55 ({numero[2:4]}) {numero[4:9]}-{numero[9:]}"


def normalizar_cnpj(texto):
    """Remove máscara; mantém letras porque o CNPJ alfanumérico vale desde jul/2026."""
    return re.sub(r"[^0-9A-Z]", "", (texto or "").upper())


def _digito_cnpj(base):
    pesos = list(range(len(base) - 7, 1, -1)) + list(range(9, 1, -1))
    soma = sum((ord(c) - 48) * p for c, p in zip(base, pesos))
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def cnpj_valido(cnpj):
    if not re.fullmatch(r"[0-9A-Z]{12}\d{2}", cnpj) or len(set(cnpj)) == 1:
        return False
    primeiro = _digito_cnpj(cnpj[:12])
    segundo = _digito_cnpj(cnpj[:12] + primeiro)
    return cnpj[12:] == primeiro + segundo


def validar_cnpj(valor):
    if not cnpj_valido(valor):
        raise ValidationError("CNPJ inválido (confira os dígitos verificadores).")


def formatar_cnpj(cnpj):
    if len(cnpj or "") != 14:
        return cnpj
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def normalizar_destino(texto):
    """Destino de SMS em E.164 sem +; com 10 ou 11 dígitos assume Brasil (DDD + número)."""
    digitos = re.sub(r"\D", "", texto or "")
    if len(digitos) in (10, 11) and digitos[0] != "0":
        digitos = "55" + digitos
    return digitos if re.fullmatch(r"[1-9]\d{9,14}", digitos) else None
