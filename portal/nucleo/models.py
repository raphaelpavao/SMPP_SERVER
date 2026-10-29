from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from .validadores import formatar_cnpj, formatar_did, normalizar_cnpj, validar_cnpj, validar_did


class Provedor(models.Model):
    """Cliente da TIP que revende os DIDs aos clientes dele."""

    razao_social = models.CharField("razão social", max_length=200)
    nome_fantasia = models.CharField("nome fantasia", max_length=120, blank=True)
    cnpj = models.CharField(
        "CNPJ", max_length=14, unique=True, validators=[validar_cnpj],
        help_text="Pode digitar com ou sem pontuação.",
    )
    email_contato = models.EmailField("e-mail de contato", blank=True)
    telefone_contato = models.CharField("telefone de contato", max_length=30, blank=True)
    ativo = models.BooleanField(
        default=True, help_text="Provedor suspenso não envia nem consulta mensagens.",
    )
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "provedor"
        verbose_name_plural = "provedores"
        ordering = ["razao_social"]

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    def save(self, *args, **kwargs):
        self.cnpj = normalizar_cnpj(self.cnpj)
        super().save(*args, **kwargs)

    @property
    def cnpj_formatado(self):
        return formatar_cnpj(self.cnpj)


class DID(models.Model):
    """Número móvel da Surf. Sem provedor, está em estoque (disponível para atribuir)."""

    numero = models.CharField(
        "número", max_length=13, unique=True, validators=[validar_did],
        help_text="E.164 sem +: 55 + DDD + 9 dígitos (ex.: 5511999990001).",
    )
    provedor = models.ForeignKey(
        Provedor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="dids", help_text="Vazio = DID em estoque.",
    )
    ativo = models.BooleanField(
        default=True, help_text="DID inativo não envia nem recebe SMS pela plataforma.",
    )
    atribuido_em = models.DateTimeField("atribuído em", null=True, blank=True, editable=False)
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "DID"
        verbose_name_plural = "DIDs"
        ordering = ["numero"]

    def __str__(self):
        return formatar_did(self.numero)


class HistoricoDID(models.Model):
    """Trilha de cadastro e de trocas de provedor de cada DID."""

    class Evento(models.TextChoices):
        CADASTRO = "cadastro", "Cadastro manual"
        IMPORTACAO = "importacao", "Importação em lote"
        TRANSFERENCIA = "transferencia", "Troca de provedor"

    did = models.ForeignKey(DID, on_delete=models.CASCADE, related_name="historico")
    evento = models.CharField(max_length=20, choices=Evento.choices)
    provedor_anterior = models.ForeignKey(
        Provedor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", verbose_name="provedor anterior",
    )
    provedor_novo = models.ForeignKey(
        Provedor, on_delete=models.PROTECT, null=True, blank=True,
        related_name="+", verbose_name="provedor novo",
    )
    usuario = models.ForeignKey(
        "Usuario", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="usuário",
    )
    data = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "histórico de DID"
        verbose_name_plural = "histórico de DIDs"
        ordering = ["-data", "-id"]

    def __str__(self):
        return f"{self.did} · {self.get_evento_display()}"


class Usuario(AbstractUser):
    """Usuário do portal. Sem provedor = equipe TIP; com provedor = só enxerga o dele."""

    provedor = models.ForeignKey(
        Provedor, on_delete=models.PROTECT, null=True, blank=True, related_name="usuarios",
        help_text="Deixe vazio para a equipe TIP. Com provedor, o usuário só vê os dados dele.",
    )

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    @property
    def e_equipe_tip(self):
        return self.provedor_id is None

    def clean(self):
        super().clean()
        if self.provedor_id and self.is_superuser:
            raise ValidationError("Usuário de provedor não pode ser superusuário.")


class Mensagem(models.Model):
    """SMS enviado (MT) ou recebido (MO) por um DID.

    O provedor é gravado na mensagem: se o DID mudar de dono, o histórico de
    mensagens continua com quem enviou ou recebeu na época.
    """

    class Direcao(models.TextChoices):
        MT = "MT", "Enviada"
        MO = "MO", "Recebida"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        NA_FILA = "na_fila", "Na fila do gateway"
        ACEITA = "aceita", "Aceita pela operadora"
        ENTREGUE = "entregue", "Entregue"
        FALHOU = "falhou", "Falhou"
        RECEBIDA = "recebida", "Recebida"

    # Status finais não voltam para trás quando um DLR atrasado chega fora de ordem.
    FINAIS = {Status.ENTREGUE, Status.FALHOU}

    direcao = models.CharField("direção", max_length=2, choices=Direcao.choices)
    provedor = models.ForeignKey(Provedor, on_delete=models.PROTECT, related_name="mensagens")
    did = models.ForeignKey(DID, on_delete=models.PROTECT, related_name="mensagens", verbose_name="DID")
    numero_remoto = models.CharField(
        "número remoto", max_length=32,
        help_text="Destino da enviada ou remetente da recebida (pode ser alfanumérico, ex.: WhatsApp).",
    )
    texto = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices)
    status_detalhe = models.CharField("detalhe do status", max_length=200, blank=True)
    id_jasmin = models.CharField("ID no gateway", max_length=64, null=True, blank=True, unique=True)
    usuario = models.ForeignKey(
        "Usuario", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="enviada por",
    )
    criada_em = models.DateTimeField("data", auto_now_add=True, db_index=True)
    atualizada_em = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["-criada_em", "-id"]
        indexes = [models.Index(fields=["provedor", "direcao", "-criada_em"])]

    def __str__(self):
        return f"{self.get_direcao_display()} {self.pk}"


class MensagemEnviada(Mensagem):
    class Meta:
        proxy = True
        verbose_name = "mensagem enviada"
        verbose_name_plural = "mensagens enviadas"


class MensagemRecebida(Mensagem):
    class Meta:
        proxy = True
        verbose_name = "mensagem recebida"
        verbose_name_plural = "mensagens recebidas"
