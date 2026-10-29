from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from . import jasmin
from .jasmin import partes_sms
from .models import DID, HistoricoDID, Mensagem, Provedor, Usuario
from .servicos import ErroEnvio, enviar_sms, importar_dids
from .validadores import cnpj_valido, normalizar_did


class ValidadoresTest(TestCase):
    def test_normaliza_formatos_comuns(self):
        for texto in ["5511999990001", "+55 (11) 99999-0001", "11 99999-0001", "(11)999990001"]:
            self.assertEqual(normalizar_did(texto), "5511999990001", texto)

    def test_recusa_fixo_ddd_invalido_e_tamanho_errado(self):
        for texto in ["551133334444", "5510999990001", "5501999990001", "551199999000", "abc"]:
            self.assertIsNone(normalizar_did(texto), texto)

    def test_cnpj_numerico_e_alfanumerico(self):
        self.assertTrue(cnpj_valido("11222333000181"))
        self.assertFalse(cnpj_valido("11222333000182"))
        self.assertFalse(cnpj_valido("11111111111111"))
        # Exemplo publicado pela Receita para o CNPJ alfanumérico.
        self.assertTrue(cnpj_valido("12ABC34501DE35"))


class ImportacaoTest(TestCase):
    def setUp(self):
        self.a = Provedor.objects.create(razao_social="Provedor A", cnpj="11222333000181")
        self.b = Provedor.objects.create(razao_social="Provedor B", cnpj="12ABC34501DE35")
        self.usuario = Usuario.objects.create_user("tip", password="x")

    def test_lista_com_faixa_mascara_repetido_e_invalido(self):
        texto = "numero\n5511999990001\n(11) 99999-0002; 5511999990001\n5511999990010 - 5511999990012\n551133334444"
        r = importar_dids(texto, self.a, self.usuario)
        self.assertEqual(r.criados, ["5511999990001", "5511999990002", "5511999990010", "5511999990011", "5511999990012"])
        self.assertEqual(r.repetidos, ["5511999990001"])
        self.assertEqual(len(r.invalidos), 1)
        self.assertEqual(DID.objects.filter(provedor=self.a).count(), 5)
        self.assertEqual(HistoricoDID.objects.filter(evento="importacao").count(), 5)

    def test_nao_rouba_did_de_outro_provedor_mas_tira_do_estoque(self):
        importar_dids("5511999990001", self.b, self.usuario)
        importar_dids("5511999990002", None, self.usuario)
        r = importar_dids("5511999990001\n5511999990002\n5511999990003", self.a, self.usuario)
        self.assertEqual(r.criados, ["5511999990003"])
        self.assertEqual(r.atribuidos_do_estoque, ["5511999990002"])
        self.assertEqual([n for n, _ in r.de_outro_provedor], ["5511999990001"])
        self.assertEqual(DID.objects.get(numero="5511999990001").provedor, self.b)

    def test_faixa_grande_demais_e_recusada(self):
        r = importar_dids("5511999900000 - 5511999999999", self.a, self.usuario)
        self.assertEqual(r.criados, [])
        self.assertIn("faixa com mais de", r.invalidos[0][1])


class IsolamentoTest(TestCase):
    def setUp(self):
        self.a = Provedor.objects.create(razao_social="Provedor A", cnpj="11222333000181")
        self.b = Provedor.objects.create(razao_social="Provedor B", cnpj="12ABC34501DE35")
        DID.objects.create(numero="5511999990001", provedor=self.a)
        self.did_b = DID.objects.create(numero="5511999990002", provedor=self.b)
        self.usuario_a = Usuario.objects.create_user("prov_a", password="x", is_staff=True, provedor=self.a)
        self.tip = Usuario.objects.create_user("tip", password="x", is_staff=True)

    def test_provedor_so_ve_os_proprios_dids_e_nao_edita(self):
        self.client.force_login(self.usuario_a)
        resp = self.client.get(reverse("admin:nucleo_did_changelist"))
        self.assertContains(resp, "99999-0001")
        self.assertNotContains(resp, "99999-0002")
        resp = self.client.get(reverse("admin:nucleo_did_change", args=[self.did_b.pk]))
        self.assertEqual(resp.status_code, 302)  # objeto fora do queryset: volta para a lista
        self.assertEqual(self.client.get(reverse("admin:nucleo_did_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:nucleo_did_importar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:nucleo_usuario_changelist")).status_code, 403)

    def test_provedor_nao_ve_historico(self):
        # O histórico revelaria por quais outros provedores o DID passou.
        self.client.force_login(self.usuario_a)
        self.assertEqual(self.client.get(reverse("admin:nucleo_historicodid_changelist")).status_code, 403)
        did_a = DID.objects.get(numero="5511999990001")
        resp = self.client.get(reverse("admin:nucleo_did_change", args=[did_a.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Troca de provedor")
        self.assertNotContains(self.client.get(reverse("admin:index")), "Últimas movimentações")

    def test_equipe_tip_troca_provedor_e_gera_historico(self):
        self.client.force_login(self.tip)
        url = reverse("admin:nucleo_did_change", args=[self.did_b.pk])
        resp = self.client.post(url, {
            "provedor": self.a.pk, "ativo": "on", "observacoes": "",
            "historico-TOTAL_FORMS": "0", "historico-INITIAL_FORMS": "0",
        })
        self.assertEqual(resp.status_code, 302, getattr(resp, "context_data", {}).get("errors"))
        self.did_b.refresh_from_db()
        self.assertEqual(self.did_b.provedor, self.a)
        ev = HistoricoDID.objects.get(did=self.did_b)
        self.assertEqual((ev.evento, ev.provedor_anterior, ev.provedor_novo), ("transferencia", self.b, self.a))

    def test_equipe_tip_exclui_did_com_historico(self):
        importar_dids("5511999990009", self.a, self.tip)
        did = DID.objects.get(numero="5511999990009")
        self.client.force_login(self.tip)
        resp = self.client.post(reverse("admin:nucleo_did_delete", args=[did.pk]), {"post": "yes"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DID.objects.filter(pk=did.pk).exists())

    def test_painel_abre_para_os_dois_perfis(self):
        for usuario in (self.tip, self.usuario_a):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)


@override_settings(PORTAL_INTERNO_TOKEN="tok123")
class MensagensTest(TestCase):
    def setUp(self):
        self.a = Provedor.objects.create(razao_social="Provedor A", cnpj="11222333000181")
        self.b = Provedor.objects.create(razao_social="Provedor B", cnpj="12ABC34501DE35")
        self.did_a = DID.objects.create(numero="5511999990001", provedor=self.a)
        self.did_b = DID.objects.create(numero="5511999990002", provedor=self.b)
        self.usuario_a = Usuario.objects.create_user("prov_a", password="x", is_staff=True, provedor=self.a)

    def enviar(self, **kw):
        with mock.patch("nucleo.jasmin.enviar", return_value="uuid-1") as enviar:
            msg = enviar_sms(self.did_a, "5511988887777", "Olá", self.usuario_a, **kw)
        return msg, enviar

    def test_envio_grava_e_passa_did_e_url_de_dlr(self):
        msg, enviar = self.enviar()
        self.assertEqual((msg.status, msg.id_jasmin, msg.provedor), ("na_fila", "uuid-1", self.a))
        origem, destino, texto, url = enviar.call_args.args
        self.assertEqual((origem, destino, texto), ("5511999990001", "5511988887777", "Olá"))
        self.assertIn("/interno/jasmin/tok123/dlr/", url)

    def test_falha_do_gateway_fica_registrada(self):
        with mock.patch("nucleo.jasmin.enviar", side_effect=jasmin.ErroJasmin("gateway indisponível")):
            msg = enviar_sms(self.did_a, "5511988887777", "Oi", None)
        self.assertEqual((msg.status, msg.status_detalhe), ("falhou", "gateway indisponível"))

    def test_did_inativo_ou_provedor_suspenso_nao_envia(self):
        self.a.ativo = False
        self.a.save()
        with self.assertRaises(ErroEnvio):
            enviar_sms(DID.objects.get(pk=self.did_a.pk), "5511988887777", "Oi", None)

    def test_dlr_atualiza_e_nao_regride(self):
        self.enviar()
        url = reverse("interno_dlr", args=["tok123"])
        resp = self.client.post(url, {"id": "uuid-1", "level": "2", "message_status": "DELIVRD", "err": "000"})
        self.assertEqual(resp.content, b"ACK/Jasmin")
        # nível 1 atrasado não pode desfazer o "entregue"
        self.client.post(url, {"id": "uuid-1", "level": "1", "message_status": "ESME_ROK"})
        self.assertEqual(Mensagem.objects.get().status, "entregue")

    def test_dlr_de_mensagem_desconhecida_pede_nova_tentativa(self):
        resp = self.client.post(reverse("interno_dlr", args=["tok123"]), {"id": "x", "level": "1", "message_status": "ESME_ROK"})
        self.assertEqual(resp.status_code, 404)

    def test_token_errado_e_recusado(self):
        resp = self.client.post(reverse("interno_mo", args=["errado"]), {"id": "1", "from": "1", "to": "5511999990001"})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Mensagem.objects.exists())

    def test_mo_ucs2_vai_para_o_dono_do_did_sem_duplicar(self):
        # coding chega como byte cru, igual ao Jasmin 0.11 manda
        dados = {"id": "mo-1", "from": "WhatsApp", "to": "5511999990002", "coding": chr(8),
                 "binary": "Código 123 ✓".encode("utf-16-be").hex(), "content": "?"}
        url = reverse("interno_mo", args=["tok123"])
        self.assertEqual(self.client.post(url, dados).content, b"ACK/Jasmin")
        self.client.post(url, dados)  # reenvio do Jasmin
        msg = Mensagem.objects.get()
        self.assertEqual((msg.direcao, msg.provedor, msg.numero_remoto, msg.texto),
                         ("MO", self.b, "WhatsApp", "Código 123 ✓"))

    def test_mo_para_did_desconhecido_confirma_sem_gravar(self):
        resp = self.client.post(reverse("interno_mo", args=["tok123"]),
                                {"id": "2", "from": "551100", "to": "5511999999999", "coding": "0", "binary": "6f69"})
        self.assertEqual(resp.content, b"ACK/Jasmin")
        self.assertFalse(Mensagem.objects.exists())

    def test_provedor_so_ve_e_so_envia_pelos_proprios_dids(self):
        Mensagem.objects.create(direcao="MO", provedor=self.b, did=self.did_b, numero_remoto="1", texto="segredo do B", status="recebida")
        self.client.force_login(self.usuario_a)
        self.assertNotContains(self.client.get(reverse("admin:nucleo_mensagemrecebida_changelist")), "segredo do B")
        resp = self.client.get(reverse("admin:nucleo_mensagemenviada_enviar"))
        self.assertContains(resp, "99999-0001")
        self.assertNotContains(resp, "99999-0002")
        with mock.patch("nucleo.jasmin.enviar", return_value="uuid-9") as enviar:
            resp = self.client.post(reverse("admin:nucleo_mensagemenviada_enviar"),
                                    {"did": self.did_b.pk, "destino": "11988887777", "texto": "oi"})
        self.assertEqual(resp.status_code, 200)  # formulário volta com erro
        enviar.assert_not_called()

    def test_contagem_de_partes(self):
        self.assertEqual(partes_sms("a" * 160), 1)
        self.assertEqual(partes_sms("a" * 161), 2)
        self.assertEqual(partes_sms("ã" * 70), 1)
        self.assertEqual(partes_sms("ã" * 71), 2)


class RespostaJasminTest(TestCase):
    def responder(self, corpo):
        resposta = mock.MagicMock()
        resposta.read.return_value = corpo.encode()
        resposta.__enter__.return_value = resposta
        return mock.patch("urllib.request.urlopen", return_value=resposta)

    def test_aceita_uuid(self):
        with self.responder('Success "b6fcec8b-0deb-4e2e-8504-54d8c1a9db16"'):
            self.assertEqual(jasmin.enviar("5511999990001", "5511988887777", "oi", "http://x/"),
                             "b6fcec8b-0deb-4e2e-8504-54d8c1a9db16")

    def test_falha_interna_com_success_vira_erro(self):
        # Caso real: canal do RabbitMQ fechado e o Jasmin ainda responde 200 "Success".
        corpo = 'Success "[Failure instance: Traceback: <class \'txamqp.client.ChannelClosed\'>: ...]"'
        with self.responder(corpo), self.assertRaises(jasmin.ErroJasmin):
            jasmin.enviar("5511999990001", "5511988887777", "oi", "http://x/")
