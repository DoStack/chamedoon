from __future__ import annotations

from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

from miniapp.auth import miniapp_login_required
from miniapp.i18n import locale_from_request, messages_for, t
from miniapp.views import _ctx, _list_fragment, _validation_message, _wants_list_fragment
from support.models import SupportTicket, TicketStatus, TicketSubject
from support.services import (
    add_user_message,
    close_ticket_by_user,
    create_ticket,
    tickets_for_user,
)


@miniapp_login_required
@xframe_options_exempt
def support_list(request: HttpRequest) -> HttpResponse:
    if not _wants_list_fragment(request):
        return render(request, "miniapp/support.html", _ctx(request))
    locale = locale_from_request(request)
    messages = messages_for(locale)
    rows = []
    for ticket in tickets_for_user(request.koolbar_user):
        rows.append(
            {
                "ticket": ticket,
                "subject_label": t(messages, f"support.subjects.{ticket.subject}"),
                "status_label": t(messages, f"status.{ticket.status}"),
            }
        )
    return _list_fragment(request, "miniapp/includes/support_list.html", _ctx(request, rows=rows))


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def support_create(request: HttpRequest) -> HttpResponse:
    error = ""
    subject = (request.POST.get("subject") if request.method == "POST" else "") or ""
    message = (request.POST.get("message") if request.method == "POST" else "") or ""
    if request.method == "POST":
        try:
            ticket = create_ticket(request.koolbar_user, {"subject": subject, "message": message})
            return redirect(f"/app/support/{ticket.pk}/?created=1")
        except ValidationError as exc:
            error = _validation_message(exc)
    return render(
        request,
        "miniapp/support_create.html",
        _ctx(
            request,
            error=error,
            subject=subject,
            message=message,
            subjects=_subject_options(request),
        ),
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def support_detail(request: HttpRequest, pk: int) -> HttpResponse:
    ticket = SupportTicket.objects.filter(pk=pk, user=request.koolbar_user).prefetch_related("messages").first()
    if ticket is None:
        raise Http404()
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "reply":
                add_user_message(ticket, request.koolbar_user, {"message": request.POST.get("message")})
                return redirect(f"/app/support/{ticket.pk}/")
            if action == "close":
                close_ticket_by_user(ticket, request.koolbar_user)
                return redirect(f"/app/support/{ticket.pk}/")
        except ValidationError as exc:
            error = _validation_message(exc)
        ticket.refresh_from_db()
    locale = locale_from_request(request)
    messages = messages_for(locale)
    thread = []
    for row in ticket.messages.all():
        if row.sender_type == "USER":
            sender = t(messages, "support.user")
        elif row.sender_type == "ADMIN":
            sender = t(messages, "support.admin")
        else:
            sender = t(messages, "support.system")
        thread.append({"sender": sender, "sender_type": row.sender_type, "message": row.message, "created_at": row.created_at})
    return render(
        request,
        "miniapp/support_detail.html",
        _ctx(
            request,
            ticket=ticket,
            thread=thread,
            error=error,
            created=request.GET.get("created") == "1",
            subject_label=t(messages, f"support.subjects.{ticket.subject}"),
            status_label=t(messages, f"status.{ticket.status}"),
            can_reply=ticket.status != TicketStatus.CLOSED,
        ),
    )


def _subject_options(request: HttpRequest) -> list[dict]:
    locale = locale_from_request(request)
    messages = messages_for(locale)
    return [
        {"value": value, "label": t(messages, f"support.subjects.{value}")}
        for value in TicketSubject.values
    ]
