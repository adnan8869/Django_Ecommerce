from django.urls import path
from . import views

urlpatterns = [
    path('stripe/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('payment_success/', views.payment_success, name='payment_success'),
    path('checkout/', views.checkout, name='checkout'),
    path('create-checkout-session/', views.create_checkout_session,
         name='create_checkout_session'),
    path('process_order/', views.process_order, name='process_order'),
    path('paid_dash/', views.paid_dash, name='paid_dash'),
    path('not_paid_dash/', views.not_paid_dash, name='not_paid_dash'),
    path('shipped_dash/', views.shipped_dash, name='shipped_dash'),
    path('not_shipped_dash/', views.not_shipped_dash, name='not_shipped_dash'),
    path('orders/<int:pk>/', views.orders, name='orders'),
]
