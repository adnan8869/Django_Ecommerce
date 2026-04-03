from django.contrib import admin
from .models import ShippingAddress, Order, OrderItem


admin.site.register(ShippingAddress)
admin.site.register(Order)
admin.site.register(OrderItem)


class OrderItemInline(admin.StackedInline):
    model = OrderItem
    extra = 0


class OrderAdmin(admin.ModelAdmin):
    model = Order
    readonly_fields = [
        'date_ordered',
        'date_paid',
        'date_shipped',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
    ]
    list_display = [
        'id',
        'email',
        'amount_paid',
        'payment_status',
        'is_paid',
        'shipped',
        'date_ordered']
    list_filter = ['payment_status', 'is_paid', 'shipped', 'date_ordered']
    search_fields = ['email', 'full_name']
    fields = [
        'user',
        'full_name',
        'email',
        'shipping_address',
        'amount_paid',
        'payment_status',
        'is_paid',
        'date_paid',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'shipped',
        'date_shipped',
        'date_ordered',
    ]
    inlines = [OrderItemInline]


admin.site.unregister(Order)
admin.site.register(Order, OrderAdmin)
