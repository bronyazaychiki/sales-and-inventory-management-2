# Standard library imports
import json
import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# Django core imports
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.shortcuts import render
from django.db import transaction

# Class-based views
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, UpdateView, DeleteView

# Authentication and permissions
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

# Third-party packages
from openpyxl import Workbook

# Local app imports
from store.models import Item
from accounts.models import Customer
from .models import Sale, Purchase, SaleDetail
from .forms import PurchaseForm


logger = logging.getLogger(__name__)

# Smallest money unit used to keep all Decimal math aligned with the
# DecimalField(decimal_places=2) columns on the Sale/SaleDetail models.
CENTS = Decimal("0.01")


class SaleValidationError(Exception):
    """Raised for user-facing validation problems while creating a sale.

    The message is safe to show directly to the user; it is caught in
    ``SaleCreateView`` and returned as a friendly error response.
    """


def is_ajax(request):
    return request.META.get('HTTP_X_REQUESTED_WITH') == 'XMLHttpRequest'


def export_sales_to_excel(request):
    # Create a workbook and select the active worksheet.
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Sales'

    # Define the column headers
    columns = [
        'ID', 'Date', 'Customer', 'Sub Total',
        'Grand Total', 'Tax Amount', 'Tax Percentage',
        'Amount Paid', 'Amount Change'
    ]
    worksheet.append(columns)

    # Fetch sales data
    sales = Sale.objects.all()

    for sale in sales:
        # Convert timezone-aware datetime to naive datetime
        if sale.date_added.tzinfo is not None:
            date_added = sale.date_added.replace(tzinfo=None)
        else:
            date_added = sale.date_added

        worksheet.append([
            sale.id,
            date_added,
            sale.customer.phone,
            sale.sub_total,
            sale.grand_total,
            sale.tax_amount,
            sale.tax_percentage,
            sale.amount_paid,
            sale.amount_change
        ])

    # Set up the response to send the file
    response = HttpResponse(
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    )
    response['Content-Disposition'] = 'attachment; filename=sales.xlsx'
    workbook.save(response)

    return response


def export_purchases_to_excel(request):
    # Create a workbook and select the active worksheet.
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = 'Purchases'

    # Define the column headers
    columns = [
        'ID', 'Item', 'Description', 'Vendor', 'Order Date',
        'Delivery Date', 'Quantity', 'Delivery Status',
        'Price per item (Ksh)', 'Total Value'
    ]
    worksheet.append(columns)

    # Fetch purchases data
    purchases = Purchase.objects.all()

    for purchase in purchases:
        # Convert timezone-aware datetime to naive datetime
        delivery_tzinfo = purchase.delivery_date.tzinfo
        order_tzinfo = purchase.order_date.tzinfo

        if delivery_tzinfo or order_tzinfo is not None:
            delivery_date = purchase.delivery_date.replace(tzinfo=None)
            order_date = purchase.order_date.replace(tzinfo=None)
        else:
            delivery_date = purchase.delivery_date
            order_date = purchase.order_date
        worksheet.append([
            purchase.id,
            purchase.item.name,
            purchase.description,
            purchase.vendor.name,
            order_date,
            delivery_date,
            purchase.quantity,
            purchase.get_delivery_status_display(),
            purchase.price,
            purchase.total_value
        ])

    # Set up the response to send the file
    response = HttpResponse(
        content_type=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    )
    response['Content-Disposition'] = 'attachment; filename=purchases.xlsx'
    workbook.save(response)

    return response


class SaleListView(LoginRequiredMixin, ListView):
    """
    View to list all sales with pagination.
    """

    model = Sale
    template_name = "transactions/sales_list.html"
    context_object_name = "sales"
    paginate_by = 10
    ordering = ['date_added']


class SaleDetailView(LoginRequiredMixin, DetailView):
    """
    View to display details of a specific sale.
    """

    model = Sale
    template_name = "transactions/saledetail.html"


def SaleCreateView(request):
    context = {"active_icon": "sales"}

    # Only AJAX POSTs submit a sale; everything else just renders the page.
    if request.method != 'POST' or not is_ajax(request=request):
        return render(
            request, "transactions/sale_create.html", context=context
        )

    # --- Parse the request body ---
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'status': 'error',
            'message': (
                'We could not read the sale data. '
                'Please refresh the page and try again.'
            )
        }, status=400)

    logger.info(f"Received sale data: {data}")

    # --- Customer (required, must exist) ---
    customer_id = data.get('customer')
    if not customer_id:
        return JsonResponse({
            'status': 'error',
            'message': 'Please select a customer before creating the sale.'
        }, status=400)
    try:
        customer = Customer.objects.get(id=int(customer_id))
    except (ValueError, TypeError, Customer.DoesNotExist):
        return JsonResponse({
            'status': 'error',
            'message': (
                'The selected customer could not be found. '
                'Please pick a customer from the list.'
            )
        }, status=400)

    # --- Items (must be a non-empty list) ---
    items = data.get('items')
    if not isinstance(items, list) or not items:
        return JsonResponse({
            'status': 'error',
            'message': 'Please add at least one item to the sale.'
        }, status=400)

    # --- Tax percentage (0-100) ---
    try:
        tax_percentage = Decimal(str(data.get('tax_percentage') or '0'))
    except InvalidOperation:
        return JsonResponse({
            'status': 'error',
            'message': 'The tax percentage must be a valid number.'
        }, status=400)
    if tax_percentage < 0 or tax_percentage > 100:
        return JsonResponse({
            'status': 'error',
            'message': 'The tax percentage must be between 0 and 100.'
        }, status=400)

    # --- Amount paid (required, valid number) ---
    try:
        amount_paid = Decimal(str(data.get('amount_paid'))).quantize(
            CENTS, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError):
        return JsonResponse({
            'status': 'error',
            'message': 'Please enter a valid amount paid.'
        }, status=400)

    # Totals are computed on the server from the stored item prices so the
    # page, the submitted payload and the database always agree (and the
    # client cannot tamper with prices or totals).
    try:
        with transaction.atomic():
            new_sale = Sale.objects.create(
                customer=customer,
                tax_percentage=float(tax_percentage),
            )

            sub_total = Decimal('0.00')
            for raw_item in items:
                try:
                    item_instance = Item.objects.get(
                        id=int(raw_item.get('id'))
                    )
                except (ValueError, TypeError, Item.DoesNotExist):
                    raise SaleValidationError(
                        'One of the items is no longer available. '
                        'Please remove it and try again.'
                    )

                try:
                    quantity = int(raw_item.get('quantity'))
                except (ValueError, TypeError):
                    raise SaleValidationError(
                        f'Please enter a valid quantity for '
                        f'"{item_instance.name}".'
                    )
                if quantity < 1:
                    raise SaleValidationError(
                        f'The quantity for "{item_instance.name}" '
                        f'must be at least 1.'
                    )
                if quantity > item_instance.quantity:
                    raise SaleValidationError(
                        f'Not enough stock for "{item_instance.name}": '
                        f'only {item_instance.quantity} left, '
                        f'but {quantity} requested.'
                    )

                price = Decimal(str(item_instance.price)).quantize(
                    CENTS, rounding=ROUND_HALF_UP
                )
                total_detail = (price * quantity).quantize(
                    CENTS, rounding=ROUND_HALF_UP
                )
                sub_total += total_detail

                SaleDetail.objects.create(
                    sale=new_sale,
                    item=item_instance,
                    price=price,
                    quantity=quantity,
                    total_detail=total_detail,
                )

                # Reduce item stock now; rolled back automatically if any
                # later validation in this transaction fails.
                item_instance.quantity -= quantity
                item_instance.save()

            sub_total = sub_total.quantize(CENTS, rounding=ROUND_HALF_UP)
            tax_amount = (
                sub_total * tax_percentage / Decimal('100')
            ).quantize(CENTS, rounding=ROUND_HALF_UP)
            grand_total = (sub_total + tax_amount).quantize(
                CENTS, rounding=ROUND_HALF_UP
            )

            if amount_paid < grand_total:
                raise SaleValidationError(
                    f'The amount paid ({amount_paid}) is less than the '
                    f'grand total ({grand_total}).'
                )

            new_sale.sub_total = sub_total
            new_sale.tax_amount = tax_amount
            new_sale.grand_total = grand_total
            new_sale.amount_paid = amount_paid
            new_sale.amount_change = (amount_paid - grand_total).quantize(
                CENTS, rounding=ROUND_HALF_UP
            )
            new_sale.save()
            logger.info(f"Sale created: {new_sale}")

    except SaleValidationError as ve:
        return JsonResponse(
            {'status': 'error', 'message': str(ve)}, status=400
        )
    except Exception as e:
        logger.error(f"Exception during sale creation: {e}")
        return JsonResponse({
            'status': 'error',
            'message': (
                'Something went wrong while saving the sale. '
                'Please try again.'
            )
        }, status=500)

    return JsonResponse({
        'status': 'success',
        'message': 'Sale created successfully!',
        'redirect': '/transactions/sales/'
    })


class SaleDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    """
    View to delete a sale.
    """

    model = Sale
    template_name = "transactions/saledelete.html"

    def get_success_url(self):
        """
        Redirect to the sales list after successful deletion.
        """
        return reverse("saleslist")

    def test_func(self):
        """
        Allow deletion only for superusers.
        """
        return self.request.user.is_superuser


class PurchaseListView(LoginRequiredMixin, ListView):
    """
    View to list all purchases with pagination.
    """

    model = Purchase
    template_name = "transactions/purchases_list.html"
    context_object_name = "purchases"
    paginate_by = 10


class PurchaseDetailView(LoginRequiredMixin, DetailView):
    """
    View to display details of a specific purchase.
    """

    model = Purchase
    template_name = "transactions/purchasedetail.html"


class PurchaseCreateView(LoginRequiredMixin, CreateView):
    """
    View to create a new purchase.
    """

    model = Purchase
    form_class = PurchaseForm
    template_name = "transactions/purchases_form.html"

    def get_success_url(self):
        """
        Redirect to the purchases list after successful form submission.
        """
        return reverse("purchaseslist")


class PurchaseUpdateView(LoginRequiredMixin, UpdateView):
    """
    View to update an existing purchase.
    """

    model = Purchase
    form_class = PurchaseForm
    template_name = "transactions/purchases_form.html"

    def get_success_url(self):
        """
        Redirect to the purchases list after successful form submission.
        """
        return reverse("purchaseslist")


class PurchaseDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    """
    View to delete a purchase.
    """

    model = Purchase
    template_name = "transactions/purchasedelete.html"

    def get_success_url(self):
        """
        Redirect to the purchases list after successful deletion.
        """
        return reverse("purchaseslist")

    def test_func(self):
        """
        Allow deletion only for superusers.
        """
        return self.request.user.is_superuser
