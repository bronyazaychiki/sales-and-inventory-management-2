import django_tables2 as tables
from .models import Item, Delivery


class ItemTable(tables.Table):
    """
    Table representation for Item model.
    """
    class Meta:
        model = Item
        template_name = "django_tables2/semantic.html"
        fields = (
            'id', 'name', 'category', 'quantity',
            'selling_price', 'expiring_date', 'vendor'
        )
        order_by_field = 'sort'


class DeliveryTable(tables.Table):
    """
    Table representation for Delivery model.
    """
    class Meta:
        model = Delivery
        fields = (
            'id', 'item', 'customer_name', 'phone_number',
            'location', 'date', 'is_delivered'
        )


class InventoryWarningTable(tables.Table):
    """
    Table used to export inventory warnings to Excel.

    Relies on the ``is_low_stock`` / ``is_expiring`` annotations added by
    store.warnings.warning_queryset() to build the warning type column.
    """
    warning = tables.Column(
        empty_values=(),
        orderable=False,
        verbose_name="Warning Type",
    )

    class Meta:
        model = Item
        fields = (
            'name', 'quantity', 'price',
            'vendor', 'expiring_date', 'warning'
        )

    def _labels(self, record):
        labels = []
        if getattr(record, 'is_low_stock', False):
            labels.append('Low stock')
        if getattr(record, 'is_expiring', False):
            labels.append('Expiring soon')
        return ', '.join(labels)

    def render_warning(self, record):
        return self._labels(record)

    def value_warning(self, record):
        return self._labels(record)
