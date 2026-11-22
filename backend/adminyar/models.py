"""@Chamedoon_adminyar_bot: who may use it, and what they did. Plan: projects/chamedoon/ADMINYAR-PLAN.md section 3."""

from __future__ import annotations

from django.db import models


class AdminRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    OWNER = "owner", "Owner"


class AdminUser(models.Model):
    """One Telegram account allowed to talk to the bot. Team accounts carry `team_account` (sara, saeid, ...)."""

    telegram_user_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=64, blank=True)
    role = models.CharField(max_length=8, choices=AdminRole.choices, default=AdminRole.ADMIN)
    team_account = models.CharField(max_length=32, blank=True, help_text="sara / saeid / niloofar / monis, blank for a personal account.")
    code = models.SlugField(
        max_length=16, unique=True, null=True, blank=True,
        help_text="Tag in this admin's links (t.me/chamedoon_home_bot?start=a_<code>). Blank: set on first use from the team account or name.",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["role", "name"]

    def __str__(self) -> str:
        return f"{self.name or self.telegram_user_id} ({self.role})"


class AuditLog(models.Model):
    """Every button and action in the bot: who, what, with which arguments, and the result. Append-only."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    admin_user = models.ForeignKey(AdminUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit")
    action = models.CharField(max_length=48, db_index=True)
    args = models.JSONField(default=dict, blank=True)
    result = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.telegram_user_id} {self.action} {self.result}"


class ReadyCategory(models.TextChoices):
    INTRO = "intro", "معرفی"
    GUIDE = "guide", "راهنما"


class ReadyPost(models.Model):
    """An intro or guide post already published in one of our channels; the admin forwards it to a person.

    Weekly, monthly and country posts are not listed here: the bot reads them from home_contentpost.
    """

    category = models.CharField(max_length=8, choices=ReadyCategory.choices)
    channel = models.ForeignKey("home.HomeChannel", on_delete=models.CASCADE, related_name="ready_posts")
    message_id = models.BigIntegerField(help_text="The post's number in the channel: t.me/<channel>/<this number>.")
    title = models.CharField(max_length=80, help_text="Short name the admin sees on the button.")
    enabled = models.BooleanField(default=True)
    views = models.PositiveIntegerField(null=True, blank=True)
    views_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["channel", "message_id"], name="adminyar_readypost_once")]

    def __str__(self) -> str:
        return f"{self.get_category_display()}: {self.title}"


class TouchVia(models.TextChoices):
    BOT = "bot", "Home bot start"
    APP = "app", "Mini App open"
    JOIN = "join", "Channel join"


class AdminTouch(models.Model):
    """A person reached us through one admin's link: home bot /start a_<code>, a Mini App link with src-a-<code>, or
    that admin's channel invite link. One row per person per admin per way in; the person's first row overall is
    their first touch (`first`)."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    admin_user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="touches")
    via = models.CharField(max_length=4, choices=TouchVia.choices)
    start_param = models.CharField(max_length=64, blank=True)
    channel = models.ForeignKey("home.HomeChannel", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    first = models.BooleanField(default=False, help_text="The person's first touch with any admin link.")
    new_user = models.BooleanField(default=False, help_text="No housing bot or Mini App record of this person before.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["telegram_user_id", "admin_user", "via", "channel"], name="adminyar_touch_once")]

    def __str__(self) -> str:
        return f"{self.telegram_user_id} via {self.via} <- {self.admin_user_id}"


class AdminInvite(models.Model):
    """One admin's own invite link to one of our channels, made by the housing bot (createChatInviteLink, name a:<code>)."""

    admin_user = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name="invites")
    channel = models.ForeignKey("home.HomeChannel", on_delete=models.CASCADE, related_name="+")
    link = models.URLField(max_length=128)
    name = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["admin_user", "channel"], name="adminyar_invite_once")]

    def __str__(self) -> str:
        return f"{self.name} {self.link}"
