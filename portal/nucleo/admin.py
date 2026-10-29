import re

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminFileFieldWidget, UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget

from .jasmin import partes_sms
from .models import DID, HistoricoDID, Mensagem, MensagemEnviada, MensagemRecebida, Provedor, Usuario
from .servicos import LIMITE_FAIXA, ErroEnvio, enviar_sms, importar_dids, registrar_cadastro_ou_troca
from .validadores import formatar_did, normalizar_cnpj, normalizar_destino, normalizar_did

admin.site.unregister(Group)
admin.site.index_title = "Painel"


class IsolamentoProvedorMixin:
    """Equipe TIP vê e edita tudo; usuário de provedor só consulta o que é dele.

    Sem permissões finas por enquanto: quem entra no admin sem provedor é da equipe TIP.
    """

    campo_provedor = "provedor"

    def _provedor(self, request):
        return request.user.provedor_id

    def _equipe_tip(self, request):
        return request.user.is_active and request.user.is_staff and not self._provedor(request)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self._provedor(request):
            qs = qs.filter(**{self.campo_provedor: self._provedor(request)})
        return qs

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_add_permission(self, request, *args):
        return self._equipe_tip(request)

    def has_change_permission(self, request, obj=None):
        return self._equipe_tip(request)

    def has_delete_permission(self, request, obj=None):
        return self._equipe_tip(request)


# --- Provedores --------------------------------------------------------------


class ProvedorForm(forms.ModelForm):
    cnpj = forms.CharField(label="CNPJ", max_length=20, help_text="Pode digitar com ou sem pontuação.")

    class Meta:
        model = Provedor
        fields = "__all__"

    def clean_cnpj(self):
        return normalizar_cnpj(self.cleaned_data["cnpj"])


@admin.register(Provedor)
class ProvedorAdmin(IsolamentoProvedorMixin, ModelAdmin):
    form = ProvedorForm
    campo_provedor = "pk"
    list_display = ["__str__", "razao_social", "cnpj_formatado", "qtd_dids", "situacao"]
    list_filter = ["ativo"]
    search_fields = ["razao_social", "nome_fantasia", "cnpj"]
    fieldsets = [
        (None, {"fields": ["razao_social", "nome_fantasia", "cnpj", "ativo"]}),
        ("Contato", {"fields": ["email_contato", "telefone_contato"]}),
        ("Outros", {"fields": ["observacoes", "criado_em", "atualizado_em"]}),
    ]
    readonly_fields = ["criado_em", "atualizado_em"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_qtd_dids=Count("dids"))

    def get_search_results(self, request, queryset, search_term):
        # Busca pelo CNPJ com ou sem máscara.
        return super().get_search_results(request, queryset, normalizar_cnpj(search_term) or search_term)

    @display(description="CNPJ", ordering="cnpj")
    def cnpj_formatado(self, obj):
        return obj.cnpj_formatado

    @display(description="DIDs", ordering="_qtd_dids")
    def qtd_dids(self, obj):
        url = reverse("admin:nucleo_did_changelist") + f"?provedor__id__exact={obj.pk}"
        return format_html('<a href="{}" class="text-primary-600 font-medium">{}</a>', url, obj._qtd_dids)

    @display(description="Situação", label={"Ativo": "success", "Suspenso": "danger"})
    def situacao(self, obj):
        return "Ativo" if obj.ativo else "Suspenso"


# --- DIDs --------------------------------------------------------------------


class DIDForm(forms.ModelForm):
    numero = forms.CharField(
        label="Número", max_length=30,
        help_text="Celular com DDD; aceita máscara. Ex.: (11) 99999-0001 ou 5511999990001.",
    )

    class Meta:
        model = DID
        fields = "__all__"

    def clean_numero(self):
        numero = normalizar_did(self.cleaned_data["numero"])
        if not numero:
            raise forms.ValidationError("Não é um celular brasileiro válido (DDD + 9 dígitos começando com 9).")
        return numero


class ImportacaoForm(forms.Form):
    numeros = forms.CharField(
        label="Números", required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 10, "placeholder": "5511999990001\n(11) 99999-0002\n5511999990100 - 5511999990199"}),
        help_text=(
            "Um por linha (ou separados por vírgula/ponto e vírgula), com ou sem máscara. "
            f"Faixas no formato início - fim, até {LIMITE_FAIXA:_} números cada.".replace("_", ".")
        ),
    )
    arquivo = forms.FileField(
        label="Ou arquivo CSV/TXT", required=False, widget=UnfoldAdminFileFieldWidget,
        help_text="Mesmo formato do campo acima. Linhas sem dígitos (cabeçalho) são ignoradas.",
    )
    provedor = forms.ModelChoiceField(
        queryset=Provedor.objects.filter(ativo=True), required=False,
        widget=UnfoldAdminSelectWidget, empty_label="— Nenhum (deixar em estoque) —",
        help_text="DIDs novos e os que estão em estoque passam para este provedor. "
        "DIDs de outro provedor não são mexidos.",
    )

    def clean(self):
        dados = super().clean()
        arquivo = dados.get("arquivo")
        if arquivo:
            if arquivo.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Arquivo maior que 5 MB.")
            try:
                dados["texto"] = arquivo.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                raise forms.ValidationError("O arquivo precisa estar em UTF-8 (CSV ou texto simples).")
        else:
            dados["texto"] = dados.get("numeros", "")
        if not re.search(r"\d", dados["texto"]):
            raise forms.ValidationError("Informe os números no campo de texto ou envie um arquivo.")
        return dados


class HistoricoInline(TabularInline):
    model = HistoricoDID
    fields = ["data", "evento", "provedor_anterior", "provedor_novo", "usuario"]
    readonly_fields = fields
    extra = 0
    can_delete = False
    tab = True
    verbose_name_plural = "Histórico"

    def has_view_permission(self, request, obj=None):
        # O histórico revela o provedor anterior: só a equipe TIP vê.
        return request.user.is_active and request.user.is_staff and request.user.e_equipe_tip

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DID)
class DIDAdmin(IsolamentoProvedorMixin, ModelAdmin):
    form = DIDForm
    list_display = ["__str__", "provedor", "situacao", "atribuido_em"]
    list_filter = ["provedor", "ativo"]
    search_fields = ["numero"]
    list_select_related = ["provedor"]
    readonly_fields = ["atribuido_em", "criado_em"]
    fields = ["numero", "provedor", "ativo", "observacoes", "atribuido_em", "criado_em"]
    inlines = [HistoricoInline]
    actions = ["ativar", "desativar"]
    actions_list = ["importar"]
    list_per_page = 50

    def get_list_filter(self, request):
        return ["ativo"] if self._provedor(request) else self.list_filter

    def get_readonly_fields(self, request, obj=None):
        # Trocar o número de um DID existente vira outro DID; melhor cadastrar novo.
        return [*self.readonly_fields, "numero"] if obj else self.readonly_fields

    def get_search_results(self, request, queryset, search_term):
        digitos = re.sub(r"\D", "", search_term)
        if digitos:
            return queryset.filter(numero__contains=digitos), False
        return super().get_search_results(request, queryset, search_term)

    @display(description="Situação", label={"Ativo": "success", "Inativo": "danger", "Em estoque": "info"})
    def situacao(self, obj):
        if not obj.ativo:
            return "Inativo"
        return "Ativo" if obj.provedor_id else "Em estoque"

    def save_model(self, request, obj, form, change):
        anterior = DID.objects.get(pk=obj.pk).provedor if change else None
        super().save_model(request, obj, form, change)
        registrar_cadastro_ou_troca(obj, anterior, not change, request.user)

    @admin.action(description="Ativar DIDs selecionados")
    def ativar(self, request, queryset):
        n = queryset.update(ativo=True)
        self.message_user(request, f"{n} DID(s) ativado(s).", messages.SUCCESS)

    @admin.action(description="Desativar DIDs selecionados")
    def desativar(self, request, queryset):
        n = queryset.update(ativo=False)
        self.message_user(request, f"{n} DID(s) desativado(s).", messages.WARNING)

    def get_deleted_objects(self, objs, request):
        # O histórico não se apaga à mão, mas vai junto quando o DID é excluído;
        # sem isto o Django exige permissão de excluir histórico e responde 403.
        apagados, contagem, sem_permissao, protegidos = super().get_deleted_objects(objs, request)
        sem_permissao.discard(HistoricoDID._meta.verbose_name)
        return apagados, contagem, sem_permissao, protegidos

    @action(description="Importar em lote", icon="upload", url_path="importar", permissions=["add"])
    def importar(self, request):
        form = ImportacaoForm(request.POST or None, request.FILES or None)
        resultado = None
        if request.method == "POST" and form.is_valid():
            resultado = importar_dids(form.cleaned_data["texto"], form.cleaned_data["provedor"], request.user)
            if resultado.total_gravado:
                self.message_user(request, f"{resultado.total_gravado} DID(s) gravado(s).", messages.SUCCESS)
            else:
                self.message_user(request, "Nenhum DID gravado. Confira o relatório abaixo.", messages.WARNING)
            form = ImportacaoForm(initial={"provedor": form.cleaned_data["provedor"]})
        return TemplateResponse(request, "admin/nucleo/did/importar.html", {
            **self.admin_site.each_context(request),
            "title": "Importar DIDs em lote",
            "opts": self.model._meta,
            "form": form,
            "resultado": resultado,
        })


@admin.register(HistoricoDID)
class HistoricoDIDAdmin(IsolamentoProvedorMixin, ModelAdmin):
    """Só para a equipe TIP: o histórico revela por quais provedores o DID passou."""

    campo_provedor = "did__provedor"
    list_display = ["data", "did", "evento", "provedor_anterior", "provedor_novo", "usuario"]
    list_filter = ["evento", "provedor_novo"]
    search_fields = ["did__numero"]
    list_select_related = ["did", "provedor_anterior", "provedor_novo", "usuario"]
    date_hierarchy = "data"

    def has_module_permission(self, request):
        return self._equipe_tip(request)

    def has_view_permission(self, request, obj=None):
        return self._equipe_tip(request)

    def has_add_permission(self, request, *args):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_search_results(self, request, queryset, search_term):
        digitos = re.sub(r"\D", "", search_term)
        if digitos:
            return queryset.filter(did__numero__contains=digitos), False
        return super().get_search_results(request, queryset, search_term)


# --- Usuários ----------------------------------------------------------------


@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ["username", "get_full_name", "email", "tipo", "is_active"]
    list_filter = ["provedor", "is_active", "is_superuser"]
    list_select_related = ["provedor"]
    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Dados pessoais", {"fields": ["first_name", "last_name", "email"]}),
        ("Acesso", {
            "fields": ["provedor", "is_active", "is_staff", "is_superuser"],
            "description": "Para acessar o portal, marque “Membro da equipe”. "
            "Usuário de provedor só consulta os dados do próprio provedor.",
        }),
        ("Datas", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (None, {"fields": ["username", "provedor", "password1", "password2"]}),
    ]

    @display(description="Tipo", label={"Equipe TIP": "info"})
    def tipo(self, obj):
        return str(obj.provedor) if obj.provedor_id else "Equipe TIP"

    def _equipe_tip(self, request):
        return request.user.is_active and request.user.is_staff and request.user.e_equipe_tip

    def has_module_permission(self, request):
        return self._equipe_tip(request)

    def has_view_permission(self, request, obj=None):
        return self._equipe_tip(request)

    def has_add_permission(self, request):
        return self._equipe_tip(request)

    def has_change_permission(self, request, obj=None):
        return self._equipe_tip(request)

    def has_delete_permission(self, request, obj=None):
        # Não apagar o próprio usuário; para tirar acesso, desmarque "Ativo".
        return self._equipe_tip(request) and obj != request.user

    def save_model(self, request, obj, form, change):
        if not change:
            obj.is_staff = True  # sem isso o usuário não entra no portal
        super().save_model(request, obj, form, change)


# --- Mensagens ---------------------------------------------------------------

# Mais que isso o Jasmin recusa (long_content_max_parts) e o custo foge do controle.
MAX_PARTES = 5


class EnvioForm(forms.Form):
    did = forms.ModelChoiceField(
        label="Enviar pelo DID", queryset=DID.objects.none(), widget=UnfoldAdminSelectWidget,
        help_text="O destinatário verá este número como remetente.",
    )
    destino = forms.CharField(
        label="Destino", max_length=30,
        help_text="Celular com DDD (ex.: 11 98888-7777) ou número internacional com código do país.",
    )
    texto = forms.CharField(
        label="Mensagem", widget=UnfoldAdminTextareaWidget(attrs={"rows": 5}),
        help_text=f"Até 160 caracteres por SMS (70 se tiver acentos como ã, õ, ç). Máximo de {MAX_PARTES} partes.",
    )

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        dids = DID.objects.filter(ativo=True, provedor__ativo=True).select_related("provedor")
        if usuario.provedor_id:
            dids = dids.filter(provedor=usuario.provedor_id)
        self.fields["did"].queryset = dids.order_by("provedor__razao_social", "numero")
        if not usuario.provedor_id:
            self.fields["did"].label_from_instance = lambda d: f"{d} — {d.provedor}"

    def clean_destino(self):
        destino = normalizar_destino(self.cleaned_data["destino"])
        if not destino:
            raise forms.ValidationError("Número inválido.")
        return destino

    def clean_texto(self):
        # Navegadores mandam quebra de linha como CRLF; no SMS vale uma só.
        texto = self.cleaned_data["texto"].replace(chr(13) + chr(10), chr(10))
        if partes_sms(texto) > MAX_PARTES:
            raise forms.ValidationError(f"Mensagem longa demais: ocupa {partes_sms(texto)} SMS (máximo {MAX_PARTES}).")
        return texto


class MensagemAdminBase(IsolamentoProvedorMixin, ModelAdmin):
    """Consulta de mensagens: ninguém edita nem apaga pelo portal."""

    direcao = None
    list_select_related = ["did", "provedor", "usuario"]
    search_fields = ["numero_remoto", "did__numero", "texto"]
    date_hierarchy = "criada_em"
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).filter(direcao=self.direcao)

    def get_search_results(self, request, queryset, search_term):
        digitos = re.sub(r"\D", "", search_term)
        if digitos and len(digitos) >= 4:
            return queryset.filter(Q(numero_remoto__contains=digitos) | Q(did__numero__contains=digitos)), False
        return super().get_search_results(request, queryset, search_term)

    def has_add_permission(self, request, *args):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Número remoto", ordering="numero_remoto")
    def remoto(self, obj):
        return formatar_did(obj.numero_remoto)

    @display(description="Mensagem")
    def texto_curto(self, obj):
        return obj.texto if len(obj.texto) <= 60 else obj.texto[:57] + "..."


@admin.register(MensagemEnviada)
class MensagemEnviadaAdmin(MensagemAdminBase):
    direcao = Mensagem.Direcao.MT
    list_display = ["criada_em", "did", "remoto", "texto_curto", "situacao", "provedor"]
    list_filter = ["status", "provedor"]
    fields = ["criada_em", "provedor", "did", "numero_remoto", "texto", "status", "status_detalhe",
              "id_jasmin", "usuario", "atualizada_em"]
    readonly_fields = fields
    actions_list = ["enviar"]

    def get_list_display(self, request):
        return self.list_display[:-1] if self._provedor(request) else self.list_display

    def get_list_filter(self, request):
        return ["status"] if self._provedor(request) else self.list_filter

    @display(description="Situação", ordering="status", label={
        "Entregue": "success", "Falhou": "danger", "Pendente": "warning",
        "Na fila do gateway": "info", "Aceita pela operadora": "info",
    })
    def situacao(self, obj):
        return obj.get_status_display()

    def has_enviar_permission(self, request):
        return request.user.is_active and request.user.is_staff

    @action(description="Enviar SMS", icon="send", url_path="enviar", permissions=["enviar"])
    def enviar(self, request):
        form = EnvioForm(request.POST or None, usuario=request.user)
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            try:
                msg = enviar_sms(d["did"], d["destino"], d["texto"], request.user)
            except ErroEnvio as erro:
                form.add_error("did", str(erro))
            else:
                if msg.status == Mensagem.Status.FALHOU:
                    self.message_user(request, f"O gateway não aceitou a mensagem: {msg.status_detalhe}", messages.ERROR)
                else:
                    self.message_user(request, f"Mensagem para {formatar_did(msg.numero_remoto)} enviada ao gateway. "
                                      "Atualize a lista para acompanhar a entrega.", messages.SUCCESS)
                return redirect("admin:nucleo_mensagemenviada_changelist")
        return TemplateResponse(request, "admin/nucleo/mensagemenviada/enviar.html", {
            **self.admin_site.each_context(request),
            "title": "Enviar SMS",
            "opts": self.model._meta,
            "form": form,
        })


@admin.register(MensagemRecebida)
class MensagemRecebidaAdmin(MensagemAdminBase):
    direcao = Mensagem.Direcao.MO
    list_display = ["criada_em", "did", "remoto", "texto_curto", "provedor"]
    list_filter = ["provedor"]
    fields = ["criada_em", "provedor", "did", "numero_remoto", "texto", "id_jasmin"]
    readonly_fields = fields

    def get_list_display(self, request):
        return self.list_display[:-1] if self._provedor(request) else self.list_display

    def get_list_filter(self, request):
        return [] if self._provedor(request) else self.list_filter
