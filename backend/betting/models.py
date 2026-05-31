from django.db import models


class BettingPlatform(models.Model):
    """A betting platform a user can fund (Bet9ja, SportyBet, ...)."""

    code = models.CharField(max_length=20, unique=True)   # e.g. "bet9ja"
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=9, blank=True, default="")
    # VTpass serviceID for this platform, used when live.
    service_id = models.CharField(max_length=40, blank=True, default="")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name
