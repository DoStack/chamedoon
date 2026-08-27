from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q

from item_requests.models import ItemRequest
from users.models import User

VISIBLE_MATCH_STATUSES = (
    "SUGGESTED",
    "ACCEPTED_BY_SUPPLY",
    "ACCEPTED_BY_DEMAND",
    "CONNECTED",
)
USER_MATCH_STATUSES = VISIBLE_MATCH_STATUSES + ("COMPLETED",)
OPEN_MATCH_STATUSES = (
    "SUGGESTED",
    "ACCEPTED_BY_SUPPLY",
    "ACCEPTED_BY_DEMAND",
)
UNFINISHED_MATCH_STATUSES = OPEN_MATCH_STATUSES + ("CONNECTED",)
MIN_VISIBLE_SCORE = Decimal("60.00")


class MatchStatus(models.TextChoices):
    SUGGESTED = "SUGGESTED", "Suggested"
    ACCEPTED_BY_SUPPLY = "ACCEPTED_BY_SUPPLY", "Accepted by supply"
    ACCEPTED_BY_DEMAND = "ACCEPTED_BY_DEMAND", "Accepted by demand"
    CONNECTED = "CONNECTED", "Connected"
    REJECTED = "REJECTED", "Rejected"
    EXPIRED = "EXPIRED", "Expired"
    COMPLETED = "COMPLETED", "Completed"


class Match(models.Model):
    demand_request = models.ForeignKey(
        ItemRequest,
        on_delete=models.CASCADE,
        related_name="demand_matches",
    )
    supply_request = models.ForeignKey(
        ItemRequest,
        on_delete=models.CASCADE,
        related_name="supply_matches",
    )
    score = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(
        max_length=32,
        choices=MatchStatus.choices,
        default=MatchStatus.SUGGESTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score", "-created_at"]
        unique_together = [("demand_request", "supply_request")]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["score"]),
        ]

    def __str__(self) -> str:
        return f"Match {self.pk} ({self.status}, {self.score})"

    def role_for(self, user: User) -> str | None:
        if self.demand_request.user_id == user.id:
            return "demand"
        if self.supply_request.user_id == user.id:
            return "supply"
        return None

    def counterpart_request(self, user: User) -> ItemRequest | None:
        role = self.role_for(user)
        if role == "demand":
            return self.supply_request
        if role == "supply":
            return self.demand_request
        return None

    @property
    def score_label(self) -> str:
        if self.score >= 80:
            return "STRONG"
        if self.score >= 60:
            return "POSSIBLE"
        return "WEAK"


class MatchRating(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="ratings")
    rater = models.ForeignKey(User, on_delete=models.CASCADE, related_name="match_ratings")
    score = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("match", "rater")]
        constraints = [
            models.CheckConstraint(condition=models.Q(score__gte=1, score__lte=5), name="rating_score_1_5"),
        ]

    def __str__(self) -> str:
        return f"Rating {self.score}/5 by {self.rater_id} on match {self.match_id}"


def matches_for_user(user: User):
    return Match.objects.filter(
        Q(demand_request__user=user) | Q(supply_request__user=user)
    )
