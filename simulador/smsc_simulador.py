"""Simulador mínimo de SMSC (SMPP 3.4) para testes locais do gateway.

Faz o papel do fornecedor:
  - aceita bind (transceiver/transmitter/receiver) com SIM_SYSTEM_ID/SIM_PASSWORD;
  - responde submit_sm com um message_id e, 1 s depois, manda o DLR (DELIVRD);
  - se o destino começar com SIM_ECO_PREFIXO, devolve o texto como MO
    (do número de destino para o remetente original);
  - responde enquire_link e unbind.

Não faz fila, retentativa nem nada de produção: é só para exercitar o fluxo.
"""
import asyncio
import itertools
import logging
import os
import struct
from datetime import datetime

BIND_RECEIVER, BIND_TRANSMITTER, BIND_TRANSCEIVER = 0x01, 0x02, 0x09
SUBMIT_SM, DELIVER_SM, UNBIND, ENQUIRE_LINK = 0x04, 0x05, 0x06, 0x15
GENERIC_NACK = 0x80000000
RESP = 0x80000000

ESME_ROK, ESME_RINVCMDID, ESME_RINVPASWD, ESME_RINVSYSID = 0x00, 0x03, 0x0E, 0x0F

TLV_RECEIPTED_MESSAGE_ID, TLV_MESSAGE_STATE, TLV_MESSAGE_PAYLOAD = 0x001E, 0x0427, 0x0424

SYSTEM_ID = os.environ.get("SIM_SYSTEM_ID", "simulador")
PASSWORD = os.environ.get("SIM_PASSWORD", "sim123")
ECO_PREFIXO = os.environ.get("SIM_ECO_PREFIXO", "5500")
PORTA = int(os.environ.get("SIM_PORTA", "2775"))

log = logging.getLogger("smsc")
ids_mensagem = itertools.count(100000)


def cstring(dados, pos):
    fim = dados.index(b"\x00", pos)
    return dados[pos:fim].decode("latin-1"), fim + 1


def parse_submit_sm(corpo):
    """Extrai os campos que interessam de um submit_sm/deliver_sm."""
    pos = 0
    _, pos = cstring(corpo, pos)                        # service_type
    src_ton, src_npi = corpo[pos], corpo[pos + 1]; pos += 2
    origem, pos = cstring(corpo, pos)
    dst_ton, dst_npi = corpo[pos], corpo[pos + 1]; pos += 2
    destino, pos = cstring(corpo, pos)
    esm_class, _, _ = corpo[pos], corpo[pos + 1], corpo[pos + 2]; pos += 3
    _, pos = cstring(corpo, pos)                        # schedule_delivery_time
    _, pos = cstring(corpo, pos)                        # validity_period
    registered_delivery = corpo[pos]
    data_coding = corpo[pos + 2]
    sm_length = corpo[pos + 4]
    pos += 5
    texto = corpo[pos:pos + sm_length]
    pos += sm_length
    while pos + 4 <= len(corpo):                        # TLVs opcionais
        tag, tamanho = struct.unpack(">HH", corpo[pos:pos + 4])
        if tag == TLV_MESSAGE_PAYLOAD:
            texto = corpo[pos + 4:pos + 4 + tamanho]
        pos += 4 + tamanho
    if esm_class & 0x40 and texto:                     # tira o UDH das mensagens concatenadas
        texto = texto[texto[0] + 1:]
    return {
        "origem": origem, "src_ton": src_ton, "src_npi": src_npi,
        "destino": destino, "dst_ton": dst_ton, "dst_npi": dst_npi,
        "esm_class": esm_class, "registered_delivery": registered_delivery,
        "data_coding": data_coding, "texto": texto,
    }


def montar_deliver_sm(origem, destino, texto, esm_class=0, data_coding=0,
                      src_ton=1, src_npi=1, dst_ton=1, dst_npi=1, tlvs=b""):
    return (
        b"\x00"                                         # service_type
        + bytes([src_ton, src_npi]) + origem.encode() + b"\x00"
        + bytes([dst_ton, dst_npi]) + destino.encode() + b"\x00"
        + bytes([esm_class, 0, 0])                      # esm_class, protocol_id, priority
        + b"\x00\x00"                                   # schedule, validity
        + bytes([0, 0, data_coding, 0, len(texto)])     # reg_delivery, replace, coding, default_id, length
        + texto + tlvs
    )


def tlv(tag, valor):
    return struct.pack(">HH", tag, len(valor)) + valor


class SessaoSMPP:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.seq = itertools.count(1)
        self.vinculado = False
        self.pode_receber = False
        self.peer = writer.get_extra_info("peername")

    async def enviar(self, command_id, corpo=b"", status=ESME_ROK, seq=None):
        seq = next(self.seq) if seq is None else seq
        self.writer.write(struct.pack(">IIII", 16 + len(corpo), command_id, status, seq) + corpo)
        await self.writer.drain()

    async def rodar(self):
        log.info("Conexão de %s", self.peer)
        try:
            while True:
                cabecalho = await self.reader.readexactly(16)
                tamanho, command_id, _, seq = struct.unpack(">IIII", cabecalho)
                corpo = await self.reader.readexactly(tamanho - 16)
                await self.tratar(command_id, seq, corpo)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            log.info("Desconectado %s", self.peer)
            self.writer.close()

    async def tratar(self, command_id, seq, corpo):
        if command_id in (BIND_RECEIVER, BIND_TRANSMITTER, BIND_TRANSCEIVER):
            system_id, pos = cstring(corpo, 0)
            senha, _ = cstring(corpo, pos)
            if system_id != SYSTEM_ID:
                status = ESME_RINVSYSID
            elif senha != PASSWORD:
                status = ESME_RINVPASWD
            else:
                status = ESME_ROK
                self.vinculado = True
                self.pode_receber = command_id != BIND_TRANSMITTER
            log.info("bind 0x%02x de %s -> status 0x%02x", command_id, system_id, status)
            await self.enviar(command_id | RESP, b"simsmsc\x00", status, seq)

        elif command_id == ENQUIRE_LINK:
            await self.enviar(ENQUIRE_LINK | RESP, seq=seq)

        elif command_id == UNBIND:
            await self.enviar(UNBIND | RESP, seq=seq)
            self.writer.close()

        elif command_id == SUBMIT_SM:
            msg = parse_submit_sm(corpo)
            message_id = str(next(ids_mensagem))
            await self.enviar(SUBMIT_SM | RESP, message_id.encode() + b"\x00", seq=seq)
            log.info("MT %s: %s -> %s (%d bytes, coding %d)", message_id, msg["origem"],
                     msg["destino"], len(msg["texto"]), msg["data_coding"])
            asyncio.create_task(self.pos_envio(message_id, msg))

        elif command_id in (DELIVER_SM | RESP, GENERIC_NACK):
            pass

        elif not command_id & RESP:
            await self.enviar(GENERIC_NACK, status=ESME_RINVCMDID, seq=seq)

    async def pos_envio(self, message_id, msg):
        await asyncio.sleep(1)
        if not self.pode_receber:
            return

        ucs2 = msg["data_coding"] == 8
        texto = msg["texto"].decode("utf-16-be" if ucs2 else "latin-1", errors="replace")

        if msg["registered_delivery"] & 0x01:
            agora = datetime.now().strftime("%y%m%d%H%M")
            recibo = (f"id:{message_id} sub:001 dlvrd:001 submit date:{agora} done date:{agora} "
                      f"stat:DELIVRD err:000 text:{texto[:20]}").encode("latin-1", errors="replace")
            tlvs = tlv(TLV_RECEIPTED_MESSAGE_ID, message_id.encode() + b"\x00") + tlv(TLV_MESSAGE_STATE, b"\x02")
            await self.enviar(DELIVER_SM, montar_deliver_sm(
                msg["destino"], msg["origem"], recibo, esm_class=0x04,
                src_ton=msg["dst_ton"], src_npi=msg["dst_npi"],
                dst_ton=msg["src_ton"], dst_npi=msg["src_npi"], tlvs=tlvs))
            log.info("DLR %s enviado", message_id)

        if msg["destino"].startswith(ECO_PREFIXO):
            await self.enviar(DELIVER_SM, montar_deliver_sm(
                msg["destino"], msg["origem"],
                ("ECO: " + texto[:60]).encode("utf-16-be") if ucs2 else ("ECO: " + texto[:150]).encode("latin-1"),
                data_coding=msg["data_coding"],
                src_ton=msg["dst_ton"], src_npi=msg["dst_npi"],
                dst_ton=msg["src_ton"], dst_npi=msg["src_npi"]))
            log.info("MO eco %s -> %s", msg["destino"], msg["origem"])


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    servidor = await asyncio.start_server(lambda r, w: SessaoSMPP(r, w).rodar(), "0.0.0.0", PORTA)
    log.info("Simulador SMSC ouvindo na porta %d (system_id=%s, eco=%s*)", PORTA, SYSTEM_ID, ECO_PREFIXO)
    async with servidor:
        await servidor.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
