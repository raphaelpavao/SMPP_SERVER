"""Cliente SMPP de teste: conecta como um cliente do gateway, envia um SMS e
mostra o que chega de volta (DLR e MO) durante alguns segundos.

    pip install -r ferramentas/requirements.txt
    python ferramentas/cliente_teste.py --usuario cliente1 --destino 5500999990001 --texto "Olá"

Com o simulador, destinos que começam com 5500 voltam como MO (eco).
A senha pode vir de --senha ou da variável SMPP_SENHA.
"""
import argparse
import logging
import os
import sys
import threading
import time

import smpplib.client
import smpplib.consts
import smpplib.exceptions
import smpplib.gsm


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--porta", type=int, default=2775)
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--senha", default=os.environ.get("SMPP_SENHA"))
    ap.add_argument("--origem", default="551140040000")
    ap.add_argument("--destino", required=True)
    ap.add_argument("--texto", default="Teste do gateway SMPP")
    ap.add_argument("--espera", type=int, default=10, help="segundos aguardando DLR/MO")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    if not args.senha:
        sys.exit("Informe --senha ou a variável SMPP_SENHA")

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.ERROR)

    cliente = smpplib.client.Client(args.host, args.porta, allow_unknown_opt_params=True)

    def enviado(pdu):
        print(f"<- submit_sm_resp seq={pdu.sequence} status={pdu.status} message_id={pdu.message_id.decode()}")

    def recebido(pdu):
        texto = pdu.short_message.decode("utf-16-be" if pdu.data_coding == 8 else "latin-1", errors="replace")
        if pdu.esm_class & 0x04:
            print(f"<- DLR  de {pdu.source_addr.decode()}: {texto}")
        else:
            print(f"<- MO   de {pdu.source_addr.decode()} para {pdu.destination_addr.decode()}: {texto}")
        return 0  # deliver_sm_resp OK

    cliente.set_message_sent_handler(enviado)
    cliente.set_message_received_handler(recebido)

    cliente.connect()
    try:
        cliente.bind_transceiver(system_id=args.usuario, password=args.senha)
    except smpplib.exceptions.PDUError as erro:
        cliente.disconnect()
        sys.exit(f"Bind recusado: {erro}")
    print(f"Conectado em {args.host}:{args.porta} como {args.usuario}")

    def ouvir():
        try:
            cliente.listen()
        except Exception:
            pass  # a conexão é fechada no fim do teste

    threading.Thread(target=ouvir, daemon=True).start()

    partes, coding, esm_class = smpplib.gsm.make_parts(args.texto)
    for parte in partes:
        pdu = cliente.send_message(
            source_addr_ton=smpplib.consts.SMPP_TON_INTL,
            source_addr_npi=smpplib.consts.SMPP_NPI_ISDN,
            source_addr=args.origem,
            dest_addr_ton=smpplib.consts.SMPP_TON_INTL,
            dest_addr_npi=smpplib.consts.SMPP_NPI_ISDN,
            destination_addr=args.destino,
            short_message=parte,
            data_coding=coding,
            esm_class=esm_class,
            registered_delivery=True,
        )
        print(f"-> submit_sm seq={pdu.sequence} {args.origem} -> {args.destino} ({len(partes)} parte(s))")

    time.sleep(args.espera)
    try:
        cliente.unbind()
    except Exception:
        pass
    cliente.disconnect()


if __name__ == "__main__":
    main()
