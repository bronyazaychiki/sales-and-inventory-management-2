import django_filters
from django import forms

from accounts.models import Vendor

from .models import Category, Item
from .warnings import (
    WARNING_EXPIRING,
    WARNING_LOW_STOCK,
    WARNING_TYPE_CHOICES,
)


class ProductFilter(django_filters.FilterSet):
    """
    Filter set for Item model.
    """
    class Meta:
        model = Item
        fields = ['name', 'category', 'vendor']


class InventoryWarningFilter(django_filters.FilterSet):
    """
    Filter set for the inventory warning page.

    Lets users narrow warnings by category, supplier (vendor) and
    warning type (low stock / expiring soon).
    """
    category = django_filters.ModelChoiceFilter(
        queryset=Category.objects.all(),
        empty_label="All categories",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all(),
        empty_label="All suppliers",
        label="Supplier",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    warning_type = django_filters.ChoiceFilter(
        choices=WARNING_TYPE_CHOICES,
        method="filter_warning_type",
        label="Warning type",
        empty_label="All warnings",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Item
        fields = ["category", "vendor", "warning_type"]

    def filter_warning_type(self, queryset, name, value):
        """Filter on the annotated warning flags from warning_queryset()."""
        if value == WARNING_LOW_STOCK:
            return queryset.filter(is_low_stock=True)
        if value == WARNING_EXPIRING:
            return queryset.filter(is_expiring=True)
        return queryset
