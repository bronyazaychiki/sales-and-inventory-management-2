"""
Module: store.warnings

Centralizes the logic for deciding which inventory items need attention,
so the dashboard counters, the warning list/filter and the export all
stay in sync.

The thresholds live in settings (``LOW_STOCK_THRESHOLD`` and
``EXPIRY_ALERT_DAYS``) so they are easy to find and adjust without
touching this logic.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import BooleanField, Case, Q, Value, When
from django.utils import timezone

from .models import Item

# Warning type identifiers, shared by the filter and the templates.
WARNING_LOW_STOCK = "low_stock"
WARNING_EXPIRING = "expiring"

WARNING_TYPE_CHOICES = [
    (WARNING_LOW_STOCK, "Low stock"),
    (WARNING_EXPIRING, "Expiring soon"),
]


def low_stock_threshold():
    """Return the configured low-stock threshold (default 10)."""
    return getattr(settings, "LOW_STOCK_THRESHOLD", 10)


def expiry_alert_days():
    """Return the configured expiry warning window in days (default 30)."""
    return getattr(settings, "EXPIRY_ALERT_DAYS", 30)


def warning_queryset():
    """Return items needing attention, annotated with the reasons.

    Each item is annotated with ``is_low_stock`` and ``is_expiring``
    boolean flags, and the queryset is limited to items that trigger at
    least one of the two warnings. Items whose expiring date has already
    passed are included in the expiring set.
    """
    expiry_cutoff = timezone.now() + timedelta(days=expiry_alert_days())

    low_stock_q = Q(quantity__lte=low_stock_threshold())
    expiring_q = Q(
        expiring_date__isnull=False,
        expiring_date__lte=expiry_cutoff,
    )

    return (
        Item.objects.annotate(
            is_low_stock=Case(
                When(low_stock_q, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            is_expiring=Case(
                When(expiring_q, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )
        .filter(low_stock_q | expiring_q)
        .select_related("category", "vendor")
    )
