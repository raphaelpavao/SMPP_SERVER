"""Configuração do portal TIP (gestão de provedores e DIDs).

Tudo que muda entre ambientes vem de variáveis de ambiente (ver .env.example).
Sem PORTAL_DB_HOST usa SQLite, o que serve para rodar os testes fora do Docker.
"""

import os
from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent


def _lista(nome, padrao=""):
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


SECRET_KEY = os.environ.get("PORTAL_SECRET_KEY", "apenas-para-desenvolvimento")
DEBUG = os.environ.get("PORTAL_DEBUG", "0") == "1"
# Nome na rede do compose, usado pelo Jasmin nos retornos de DLR/MO.
ALLOWED_HOSTS = _lista("PORTAL_ALLOWED_HOSTS", "localhost,127.0.0.1") + ["portal.interno"]
CSRF_TRUSTED_ORIGINS = _lista("PORTAL_CSRF_TRUSTED_ORIGINS", "http://localhost:8000")

INSTALLED_APPS = [
    "unfold",  # precisa vir antes do django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "nucleo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # vem antes dos templates do unfold
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

if os.environ.get("PORTAL_DB_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.environ["PORTAL_DB_HOST"],
            "PORT": os.environ.get("PORTAL_DB_PORT", "5432"),
            "NAME": os.environ.get("PORTAL_DB_NAME", "portal"),
            "USER": os.environ.get("PORTAL_DB_USER", "portal"),
            "PASSWORD": os.environ.get("PORTAL_DB_PASSWORD", ""),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "nucleo.Usuario"
LOGIN_URL = "admin:login"
LOGIN_REDIRECT_URL = "admin:index"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
# Sempre em português, independente do idioma do navegador (sem LocaleMiddleware).
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Sem manifesto: os testes rodam sem collectstatic.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Atrás de proxy com HTTPS (produção), o proxy informa o esquema original.
if os.environ.get("PORTAL_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# --- Integração com o Jasmin ------------------------------------------------

# API HTTP do Jasmin para envio, com um usuário do Jasmin dedicado ao portal.
JASMIN_HTTP_URL = os.environ.get("PORTAL_JASMIN_URL", "http://jasmin:1401")
JASMIN_USUARIO = os.environ.get("PORTAL_JASMIN_USUARIO", "portal")
JASMIN_SENHA = os.environ.get("PORTAL_JASMIN_SENHA", "")
# Endereço do portal visto pelo Jasmin (DLR/MO) e token que protege essas rotas.
PORTAL_URL_INTERNA = os.environ.get("PORTAL_URL_INTERNA", "http://portal.interno:8000")
PORTAL_INTERNO_TOKEN = os.environ.get("PORTAL_INTERNO_TOKEN", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "nucleo": {"handlers": ["console"], "level": "INFO"},
        # Erros 500 com traceback no log do container
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

# --- Aparência (django-unfold) ----------------------------------------------

UNFOLD = {
    "SITE_TITLE": "TIP Brasil · SMS",
    "SITE_HEADER": "TIP Brasil",
    "SITE_SUBHEADER": "Gestão de DIDs e SMS",
    "SITE_SYMBOL": "sms",
    "SITE_URL": None,
    "SITE_FAVICONS": [
        {"rel": "icon", "type": "image/svg+xml", "href": lambda request: static("nucleo/favicon.svg")},
    ],
    "SHOW_VIEW_ON_SITE": False,
    "DASHBOARD_CALLBACK": "nucleo.painel.dashboard",
    "COLORS": {
        # Azul provisório; trocar pelas cores da marca TIP quando definidas.
        "primary": {
            "50": "oklch(97% .014 254.604)",
            "100": "oklch(93.2% .032 255.585)",
            "200": "oklch(88.2% .059 254.128)",
            "300": "oklch(80.9% .105 251.813)",
            "400": "oklch(70.7% .165 254.624)",
            "500": "oklch(62.3% .214 259.815)",
            "600": "oklch(54.6% .245 262.881)",
            "700": "oklch(48.8% .243 264.376)",
            "800": "oklch(42.4% .199 265.638)",
            "900": "oklch(37.9% .146 265.522)",
            "950": "oklch(28.2% .091 267.935)",
        },
    },
    "SIDEBAR": {
        "show_search": False,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Operação"),
                "items": [
                    {
                        "title": _("Painel"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                    {
                        "title": _("Provedores"),
                        "icon": "corporate_fare",
                        "link": reverse_lazy("admin:nucleo_provedor_changelist"),
                    },
                    {
                        "title": _("DIDs"),
                        "icon": "sim_card",
                        "link": reverse_lazy("admin:nucleo_did_changelist"),
                    },
                    {
                        "title": _("Histórico de DIDs"),
                        "icon": "history",
                        "link": reverse_lazy("admin:nucleo_historicodid_changelist"),
                        "permission": "nucleo.painel.e_equipe_tip",
                    },
                ],
            },
            {
                "title": _("Mensagens"),
                "items": [
                    {
                        "title": _("Enviar SMS"),
                        "icon": "send",
                        "link": reverse_lazy("admin:nucleo_mensagemenviada_enviar"),
                    },
                    {
                        "title": _("Enviadas"),
                        "icon": "outbox",
                        "link": reverse_lazy("admin:nucleo_mensagemenviada_changelist"),
                    },
                    {
                        "title": _("Recebidas"),
                        "icon": "inbox",
                        "link": reverse_lazy("admin:nucleo_mensagemrecebida_changelist"),
                    },
                ],
            },
            {
                "title": _("Administração"),
                "items": [
                    {
                        "title": _("Usuários"),
                        "icon": "group",
                        "link": reverse_lazy("admin:nucleo_usuario_changelist"),
                        "permission": "nucleo.painel.e_equipe_tip",
                    },
                ],
            },
        ],
    },
}
