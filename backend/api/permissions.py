from __future__ import annotations

import hmac

from django.conf import settings
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from users.models import User


class IsTelegramUser(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        if not isinstance(request.user, User) or not request.user.is_authenticated:
            raise NotAuthenticated()
        if not request.user.is_active:
            raise NotAuthenticated()
        return True


class IsBotService(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        expected = (settings.BOT_SERVICE_SECRET or "").strip()
        provided = (request.headers.get("X-Bot-Secret") or "").strip()
        if not expected or not provided or len(provided) != len(expected):
            raise PermissionDenied("Invalid bot credentials.")
        if not hmac.compare_digest(provided, expected):
            raise PermissionDenied("Invalid bot credentials.")
        return True
