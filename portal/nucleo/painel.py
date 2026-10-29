"""Página inicial do admin e callbacks usados na configuração do Unfold."""

from django.db.models import Count, Q

from .models import DID, HistoricoDID, Provedor


def e_equipe_tip(request):
    return request.user.is_authenticated and request.user.e_equipe_tip


def dashboard(request, context):
    dids = DID.objects.all()
    if not request.user.e_equipe_tip:
        dids = dids.filter(provedor=request.user.provedor_id)

    totais = dids.aggregate(
        total=Count("id"),
        atribuidos=Count("id", filter=Q(provedor__isnull=False)),
        estoque=Count("id", filter=Q(provedor__isnull=True)),
        inativos=Count("id", filter=Q(ativo=False)),
    )
    if request.user.e_equipe_tip:
        indicadores = [
            ("Provedores ativos", Provedor.objects.filter(ativo=True).count(), "corporate_fare"),
            ("DIDs atribuídos", totais["atribuidos"], "sim_card"),
            ("DIDs em estoque", totais["estoque"], "inventory_2"),
            ("DIDs inativos", totais["inativos"], "block"),
        ]
    else:
        indicadores = [
            ("Meus DIDs", totais["total"], "sim_card"),
            ("DIDs inativos", totais["inativos"], "block"),
        ]
    # O histórico revela por quais provedores o DID passou: só a equipe TIP vê.
    ultimos_eventos = None
    if request.user.e_equipe_tip:
        ultimos_eventos = HistoricoDID.objects.select_related(
            "did", "provedor_anterior", "provedor_novo", "usuario")[:10]
    context.update(indicadores=indicadores, ultimos_eventos=ultimos_eventos)
    return context
