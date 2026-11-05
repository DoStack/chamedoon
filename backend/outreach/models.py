from __future__ import annotations

from django.db import models


class OutreachStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"
    HELD = "HELD", "Held for review"


class OutreachKind(models.TextChoices):
    """The message models of docs/outreach.md. C* is courier, S* a housing seeker, P* a housing poster."""

    C1 = "C1", "C1 courier sender, traveler found"
    C2 = "C2", "C2 courier traveler"
    C3 = "C3", "C3 Nowruz wave"
    S1 = "S1", "S1 seeker, exact listings found"
    S1B = "S1B", "S1b seeker, budget unknown"
    S2 = "S2", "S2 seeker, nothing found"
    P1 = "P1", "P1 poster, your ad is on Chamedoon"
    P1Q = "P1Q", "P1q poster, opens a chat"
    P3 = "P3", "P3 stats (after a chat)"
    P5 = "P5", "P5 still available? (after a chat)"
    P6 = "P6", "P6 agent"


# The first message to a person. P3 and P5 only follow a chat.
FIRST_CONTACT_KINDS = (
    OutreachKind.C1,
    OutreachKind.C2,
    OutreachKind.C3,
    OutreachKind.S1,
    OutreachKind.S1B,
    OutreachKind.S2,
    OutreachKind.P1,
    OutreachKind.P1Q,
    OutreachKind.P6,
)


class OutreachMessage(models.Model):
    """One Telegram DM from a sending account. Courier rows hang on a demand, housing rows on a listing and have no User."""

    kind = models.CharField(max_length=4, choices=OutreachKind.choices, default=OutreachKind.C1, db_index=True)
    recipient = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="outreach_messages",
        null=True,
        blank=True,
    )
    recipient_username = models.CharField(max_length=32, db_index=True)
    recipient_name = models.CharField(max_length=128, blank=True, help_text="Telegram display name, only for the greeting.")
    demand_request = models.OneToOneField(
        "item_requests.ItemRequest",
        on_delete=models.CASCADE,
        related_name="outreach_message",
        null=True,
        blank=True,
    )
    supply_request = models.ForeignKey(
        "item_requests.ItemRequest",
        on_delete=models.SET_NULL,
        related_name="outreach_supply_messages",
        null=True,
        blank=True,
        help_text="C2: the traveler's imported trip.",
    )
    listing = models.ForeignKey(
        "home.HomeListing",
        on_delete=models.SET_NULL,
        related_name="outreach_messages",
        null=True,
        blank=True,
        help_text="The housing listing this message is about.",
    )
    country = models.CharField(max_length=2, blank=True, help_text="Where the recipient is: their send window and the funnel.")
    city = models.CharField(max_length=64, blank=True, help_text="The metro, one of the launch cities.")
    source_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="When the ad or request was posted. Newest go first.")
    variant = models.CharField(max_length=8, blank=True, help_text="Which wording was used: the funnel compares them.")
    extra = models.JSONField(default=dict, blank=True, help_text="What the text needs: budget, match count, listing count, follow-up numbers.")
    batch = models.CharField(max_length=32, blank=True, db_index=True, help_text="Rollout batch, approved as one.")
    conversation = models.ForeignKey(
        "outreach.OutreachConversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="A follow-up stays with this chat's account.",
    )
    member_at = models.DateTimeField(null=True, blank=True, help_text="Opened the Mini App or started the bot, after this message.")
    bot_started_at = models.DateTimeField(null=True, blank=True)
    active_at = models.DateTimeField(null=True, blank=True, help_text="Confirmed an alert or opened their own listing.")
    stopped_at = models.DateTimeField(null=True, blank=True, help_text="Said they do not want messages, or asked for removal.")
    matches = models.ManyToManyField("matching.Match", related_name="outreach_messages", blank=True)
    text = models.TextField()
    token = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=16,
        choices=OutreachStatus.choices,
        default=OutreachStatus.QUEUED,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)
    recipient_telegram_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    account = models.ForeignKey(
        "outreach.OutreachAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="Planned or actual sending account. Another account takes over when this one cannot send.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "outreach message"
        verbose_name_plural = "outreach messages"

    def __str__(self) -> str:
        return f"@{self.recipient_username} ({self.kind} {self.status})"


class OutreachOptOut(models.Model):
    telegram_username = models.CharField(max_length=32, blank=True, db_index=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source = models.CharField(max_length=16, default="reply")
    block_listings = models.BooleanField(default=False, help_text="They asked for removal: their new posts are not imported either.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "outreach opt-out"
        verbose_name_plural = "outreach opt-outs"

    def __str__(self) -> str:
        return f"@{self.telegram_username}" if self.telegram_username else str(self.telegram_user_id)


class OutreachState(models.Model):
    """Singleton: sending pause and worker health."""

    stopped = models.BooleanField(
        default=False,
        help_text="Stop all sending right away (no redeploy needed). The flood rules set it too; only a person clears it.",
    )
    paused_until = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=64, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_built_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "outreach state"
        verbose_name_plural = "outreach state"

    def __str__(self) -> str:
        return "Outreach state"

    @classmethod
    def load(cls) -> "OutreachState":
        state, _created = cls.objects.get_or_create(pk=1)
        return state


class OutreachSettings(models.Model):
    """Singleton: outreach controls an operator changes in the admin. Blank uses the server environment."""

    enabled = models.BooleanField(
        null=True,
        blank=True,
        help_text="Build the outreach queue (cron and worker). Blank: OUTREACH_ENABLED.",
    )
    sending_enabled = models.BooleanField(
        null=True,
        blank=True,
        help_text="Hand queued messages to the sender. Blank: OUTREACH_SENDING_ENABLED.",
    )
    daily_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Most messages attempted in 24 hours, all accounts together: the rollout step (5, 10, 30). Blank: OUTREACH_DAILY_LIMIT.",
    )
    account_daily_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Default messages in 24 hours for one sending account (an account row can override it). Blank: OUTREACH_ACCOUNT_DAILY_LIMIT, 30.",
    )
    account_hourly_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Default messages in 60 minutes for one sending account (an account row can override it). Blank: OUTREACH_ACCOUNT_HOURLY_LIMIT, 3.",
    )
    min_gap_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Default least minutes between two messages from one account; a random part is added on top. Blank: OUTREACH_MIN_GAP_MINUTES, 15.",
    )
    send_start_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="First sending hour, on the recipient city's clock (0 to 23). Blank: OUTREACH_SEND_START_HOUR, 10.",
    )
    send_end_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Sending stops at this hour, on the recipient city's clock (1 to 24). Blank: OUTREACH_SEND_END_HOUR, 20.",
    )
    enabled_kinds = models.CharField(
        max_length=64,
        blank=True,
        help_text="Models that may be built, comma separated, e.g. S1,S1B,S2,P1,P1Q. Blank: OUTREACH_ENABLED_KINDS.",
    )
    auto_queue = models.BooleanField(
        default=False,
        help_text="Off: new messages wait as Held until a batch is approved. On: they go straight to the queue.",
    )
    agent_enabled = models.BooleanField(default=False, help_text="Let the agent answer replies. Off: replies are stored and nothing is answered.")
    agent_model = models.CharField(max_length=96, blank=True, help_text="OmniRoute model or combo for the agent. Blank: the server default.")
    transcript_chat_id = models.BigIntegerField(
        null=True, blank=True, help_text="Private group that sees every chat. The housing bot must be a member."
    )
    alert_chat_id = models.BigIntegerField(
        null=True, blank=True, help_text="Where Mohammad hears about limits and questions. Blank: the transcript group."
    )
    operator_ids = models.CharField(
        max_length=200, blank=True, help_text="Telegram ids allowed to take a chat from the agent in the group, comma separated."
    )
    min_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Lowest match score (0 to 100) that gets a message. Blank: OUTREACH_MIN_SCORE.",
    )
    max_post_age_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Only ads and requests posted in the last N days. Blank: OUTREACH_MAX_POST_AGE_DAYS.",
    )
    cooldown_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Days between two follow-ups to one person. First messages never repeat. Blank: OUTREACH_COOLDOWN_DAYS.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "outreach settings"
        verbose_name_plural = "outreach settings"

    def __str__(self) -> str:
        return "Outreach settings"

    @classmethod
    def load(cls) -> "OutreachSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class OutreachAccount(models.Model):
    """A Telegram account the worker sends from: sina, sara, monis."""

    name = models.SlugField(max_length=32, unique=True, help_text="Name the worker sends as `account`, e.g. sina.")
    enabled = models.BooleanField(default=True, help_text="Off: claims for this account get nothing. The flood rules switch it off too.")
    daily_limit = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Most messages in 24 hours. Blank: the default in outreach settings."
    )
    hourly_limit = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Most messages in 60 minutes. Blank: the default in outreach settings."
    )
    min_gap_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Least minutes between two messages. Blank: the default in outreach settings."
    )
    last_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Last time a message was handed to this account."
    )
    next_send_at = models.DateTimeField(null=True, blank=True, help_text="The random gap after the last message ends here.")
    paused_until = models.DateTimeField(null=True, blank=True, help_text="PEER_FLOOD, FLOOD_WAIT: this account alone waits.")
    pause_reason = models.CharField(max_length=64, blank=True)
    session_ok = models.BooleanField(default=True, help_text="Off: Telegram revoked the login. Only Mohammad can log it in again.")
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "outreach account"
        verbose_name_plural = "outreach accounts"

    def __str__(self) -> str:
        return self.name


class OutreachAccountEvent(models.Model):
    """What Telegram said about an account. The stop rules count these."""

    account = models.ForeignKey(OutreachAccount, on_delete=models.CASCADE, related_name="events")
    code = models.CharField(max_length=64, db_index=True)
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.account_id} {self.code}"


class ConversationState(models.TextChoices):
    OPEN = "open", "Agent answers"
    HUMAN = "human", "Taken by Mohammad"
    STOPPED = "stopped", "They do not want messages"


class OutreachConversation(models.Model):
    """One person, one sending account, for good. Exists from the first reply."""

    telegram_user_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=32, blank=True, db_index=True)
    account = models.ForeignKey(OutreachAccount, on_delete=models.PROTECT, related_name="conversations")
    first_message = models.ForeignKey(OutreachMessage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    state = models.CharField(max_length=8, choices=ConversationState.choices, default=ConversationState.OPEN, db_index=True)
    awaiting = models.JSONField(default=dict, blank=True, help_text="What the agent asked and waits for: which listing, a yes for the phone.")
    hooks_done = models.JSONField(default=list, blank=True, help_text="Hooks already used: bot, field, phone. Each only once.")
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    last_outbound_at = models.DateTimeField(null=True, blank=True)
    model_down_since = models.DateTimeField(null=True, blank=True)
    model_down_told = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_inbound_at", "-id"]

    def __str__(self) -> str:
        return f"@{self.username or self.telegram_user_id} via {self.account_id}"


class TurnDirection(models.TextChoices):
    IN = "in", "From them"
    OUT = "out", "From the agent"


class TurnStatus(models.TextChoices):
    PENDING = "pending", "Waiting for an answer"
    ANSWERING = "answering", "Handed to the worker"
    DONE = "done", "Answered or nothing to answer"


class OutreachTurn(models.Model):
    """One line of a chat. Inbound lines are data, never instructions."""

    conversation = models.ForeignKey(OutreachConversation, on_delete=models.CASCADE, related_name="turns")
    direction = models.CharField(max_length=3, choices=TurnDirection.choices)
    text = models.TextField(blank=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    has_photo = models.BooleanField(default=False)
    photo_key = models.CharField(max_length=160, blank=True, help_text="Where the photo sent in the chat waits in storage.")
    status = models.CharField(max_length=10, choices=TurnStatus.choices, default=TurnStatus.DONE, db_index=True)
    reply_due_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="The natural delay of 1 to 5 minutes ends here.")
    claimed_at = models.DateTimeField(null=True, blank=True)
    action = models.CharField(max_length=24, blank=True, help_text="What the agent did, from the closed list.")
    action_detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "direction", "telegram_message_id"],
                condition=models.Q(telegram_message_id__isnull=False),
                name="outreach_turn_once",
            )
        ]

    def __str__(self) -> str:
        return f"{self.conversation_id} {self.direction} {self.text[:40]}"
