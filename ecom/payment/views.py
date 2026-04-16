from django.shortcuts import redirect, render
from django.urls import reverse
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from cart.cart import Cart
from payment.forms import ShippingForm
from payment.models import ShippingAddress, Order, OrderItem
from django.contrib import messages
from store.models import Profile
from decimal import Decimal, ROUND_HALF_UP
import json
import datetime
import logging
import urllib.error
import urllib.request
import stripe


logger = logging.getLogger(__name__)


def _build_shipping_payload(post_data):
    return {
        'shipping_full_name': post_data.get('shipping_full_name', ''),
        'shipping_email': post_data.get('shipping_email', ''),
        'shipping_address1': post_data.get('shipping_address1', ''),
        'shipping_address2': post_data.get('shipping_address2', ''),
        'shipping_city': post_data.get('shipping_city', ''),
        'shipping_state': post_data.get('shipping_state', ''),
        'shipping_zip_code': post_data.get('shipping_zip_code', ''),
        'shipping_country': post_data.get('shipping_country', ''),
    }


def _safe_stripe_attr(obj, attr_name, default=None):
    if obj is None:
        return default

    try:
        return getattr(obj, attr_name)
    except AttributeError:
        if isinstance(obj, dict):
            return obj.get(attr_name, default)
        return default


def _clear_cart_after_payment(request):
    request.session.pop('session_key', None)
    if request.user.is_authenticated:
        Profile.objects.filter(user=request.user).update(old_cart='')


def _update_order_payment_state(
        order,
        *,
        is_paid,
        payment_status,
        stripe_checkout_session_id=None,
        stripe_payment_intent_id=None):
    order.is_paid = is_paid
    order.payment_status = payment_status
    if stripe_checkout_session_id:
        order.stripe_checkout_session_id = stripe_checkout_session_id
    if stripe_payment_intent_id:
        order.stripe_payment_intent_id = stripe_payment_intent_id
    order.save()


def _resolve_order_from_stripe_object(stripe_object):
    metadata = _safe_stripe_attr(stripe_object, 'metadata', {})
    metadata_order_id = _safe_stripe_attr(metadata, 'order_id')
    if metadata_order_id:
        order = Order.objects.filter(id=metadata_order_id).first()
        if order:
            return order

    stripe_payment_intent_id = str(_safe_stripe_attr(stripe_object, 'id', '') or '')
    if stripe_payment_intent_id:
        order = Order.objects.filter(
            stripe_payment_intent_id=stripe_payment_intent_id,
        ).first()
        if order:
            return order

    return None


def _mark_order_paid_idempotent(order, *, stripe_payment_intent_id=''):
    # Stripe can redeliver the same event, so repeated paid updates must be no-ops.
    if order.is_paid and order.payment_status == Order.PaymentStatus.PAID:
        return

    _update_order_payment_state(
        order,
        is_paid=True,
        payment_status=Order.PaymentStatus.PAID,
        stripe_payment_intent_id=stripe_payment_intent_id,
    )


def _mark_order_failed_idempotent(order, *, stripe_payment_intent_id=''):
    # Never downgrade a settled payment because of late/duplicate failed events.
    if order.is_paid and order.payment_status == Order.PaymentStatus.PAID:
        return

    if (not order.is_paid and
            order.payment_status == Order.PaymentStatus.FAILED):
        return

    _update_order_payment_state(
        order,
        is_paid=False,
        payment_status=Order.PaymentStatus.FAILED,
        stripe_payment_intent_id=stripe_payment_intent_id,
    )


def _send_payment_confirmation_email(order):
    api_key = settings.SENDGRID_API_KEY
    sender_email = settings.PAYMENT_SENDER_EMAIL
    recipient_email = (order.email or '').strip()

    if not api_key or not sender_email or not recipient_email:
        missing = []
        if not api_key:
            missing.append('SENDGRID_API_KEY')
        if not sender_email:
            missing.append('PAYMENT_SENDER_EMAIL')
        if not recipient_email:
            missing.append('customer email')
        return False, f"Missing: {', '.join(missing)}"

    subject = f'Payment Confirmation - Order #{order.id}'
    order_date = order.date_ordered.strftime('%Y-%m-%d %H:%M UTC')
    message = (
        f'Hi {order.full_name},\n\n'
        f'We have received your payment successfully.\n'
        f'Order ID: {order.id}\n'
        f'Amount Paid: ${order.amount_paid}\n'
        f'Order Date: {order_date}\n\n'
        f'Thank you for shopping with us.'
    )

    personalizations = [{'to': [{'email': recipient_email}]}]
    if settings.PAYMENT_ADMIN_EMAIL:
        personalizations[0]['bcc'] = [{'email': settings.PAYMENT_ADMIN_EMAIL}]

    payload = {
        'personalizations': personalizations,
        'from': {'email': sender_email},
        'subject': subject,
        'content': [{'type': 'text/plain', 'value': message}],
    }

    req = urllib.request.Request(
        'https://api.sendgrid.com/v3/mail/send',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 202:
                return True, ''
            return False, f'SendGrid status {response.status}'
    except urllib.error.HTTPError as exc:
        raw_error = exc.read().decode('utf-8', errors='ignore')
        logger.warning('SendGrid HTTPError %s: %s', exc.code, raw_error)
        if exc.code == 401:
            return False, 'Invalid SendGrid API key (401).'
        if exc.code == 403:
            return False, (
                'Sender email is not verified in SendGrid (403).'
            )
        return False, f'SendGrid HTTP {exc.code}'
    except urllib.error.URLError as exc:
        logger.warning('SendGrid URLError: %s', exc)
        return False, 'Network error while contacting SendGrid.'
    except TimeoutError:
        logger.warning('SendGrid request timed out')
        return False, 'SendGrid request timed out.'


def _create_order_from_current_cart(
        request,
        *,
        is_paid=False,
        payment_status=Order.PaymentStatus.PENDING,
        clear_cart=False,
        stripe_checkout_session_id=None,
        stripe_payment_intent_id=None):
    cart = Cart(request)
    cart_products = cart.get_prods()
    quantities = cart.get_quants()
    totals = cart.cart_total()

    if not cart_products:
        messages.error(request, 'Your cart is empty.')
        return None

    my_shipping = request.session.get('my_shipping')
    if not my_shipping:
        messages.error(
            request,
            'Shipping details are missing. Please try again.')
        return None

    full_name = my_shipping.get('shipping_full_name', '')
    email = my_shipping.get('shipping_email', '')
    shipping_address = (
        f"{my_shipping.get('shipping_address1', '')}\n"
        f"{my_shipping.get('shipping_address2', '')}\n"
        f"{my_shipping.get('shipping_city', '')}\n"
        f"{my_shipping.get('shipping_state', '')}\n"
        f"{my_shipping.get('shipping_zip_code', '')}"
    )
    amount_paid = totals

    if request.user.is_authenticated:
        user = request.user
        create_order = Order(
            user=user,
            full_name=full_name,
            email=email,
            shipping_address=shipping_address,
            amount_paid=amount_paid,
            is_paid=is_paid,
            payment_status=payment_status,
            stripe_checkout_session_id=stripe_checkout_session_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        create_order.save()
        order_id = create_order.id
        created_items = 0
        for product in cart_products:
            product_id = product.id
            if product.is_sale:
                price = product.sale_price
            else:
                price = product.price
            quantity = quantities.get(str(product_id))
            if quantity:
                create_order_item = OrderItem(
                    order_id=order_id,
                    product_id=product_id,
                    user=user,
                    quantity=quantity,
                    price=price,
                )
                create_order_item.save()
                created_items += 1

        if created_items == 0:
            create_order.delete()
            messages.error(
                request, 'No order items were created. Please try checkout again.')
            return None

        if clear_cart:
            _clear_cart_after_payment(request)
        return create_order

    create_order = Order(
        full_name=full_name,
        email=email,
        shipping_address=shipping_address,
        amount_paid=amount_paid,
        is_paid=is_paid,
        payment_status=payment_status,
        stripe_checkout_session_id=stripe_checkout_session_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
    )
    create_order.save()
    order_id = create_order.id
    created_items = 0
    for product in cart_products:
        product_id = product.id
        if product.is_sale:
            price = product.sale_price
        else:
            price = product.price
        quantity = quantities.get(str(product_id))
        if quantity:
            create_order_item = OrderItem(
                order_id=order_id,
                product_id=product_id,
                quantity=quantity,
                price=price,
            )
            create_order_item.save()
            created_items += 1

    if created_items == 0:
        create_order.delete()
        messages.error(
            request, 'No order items were created. Please try checkout again.')
        return None

    if clear_cart:
        _clear_cart_after_payment(request)
    return create_order


def orders(request, pk):
    if request.user.is_authenticated and request.user.is_superuser:
        order = Order.objects.get(id=pk)
        items = OrderItem.objects.filter(order_id=pk).select_related('product')
        if request.POST:
            order_qs = Order.objects.filter(id=pk)
            shipping_status = request.POST.get('shipping_status')
            payment_status = request.POST.get('payment_status')

            if shipping_status in ['true', 'false']:
                if shipping_status == 'true':
                    now = datetime.datetime.now()
                    order_qs.update(shipped=True, date_shipped=now)
                else:
                    order_qs.update(shipped=False, date_shipped=None)
                messages.success(request, 'Shipping status updated.')

            if payment_status in ['true', 'false']:
                if payment_status == 'true':
                    now = datetime.datetime.now()
                    order_qs.update(
                        is_paid=True,
                        payment_status=Order.PaymentStatus.PAID,
                        date_paid=now,
                    )
                else:
                    order_qs.update(
                        is_paid=False,
                        payment_status=Order.PaymentStatus.PENDING,
                        date_paid=None,
                    )
                messages.success(request, 'Payment status updated.')

            return redirect('orders', pk=pk)
        return render(request, 'payment/orders.html',
                      {"order": order, "items": items})
    else:
        messages.error(
            request,
            'You do not have permission to access this page.')
        return redirect('home')


def not_shipped_dash(request):
    if request.user.is_authenticated and request.user.is_superuser:
        orders = Order.objects.filter(shipped=False)
        if request.POST:
            status = request.POST.get('shipping_status')
            num = request.POST.get('num')
            order = Order.objects.filter(id=num)
            now = datetime.datetime.now()
            order.update(shipped=True, date_shipped=now)
            messages.success(request, 'Shipping status updated.')
            return redirect('home')
        return render(request, 'payment/not_shipped_dash.html',
                      {"orders": orders})
    else:
        messages.error(
            request,
            'You do not have permission to access this page.')
        return redirect('home')


def shipped_dash(request):
    if request.user.is_authenticated and request.user.is_superuser:
        orders = Order.objects.filter(shipped=True)
        if request.POST:
            status = request.POST.get('shipping_status')
            num = request.POST.get('num')
            order = Order.objects.filter(id=num)
            now = datetime.datetime.now()
            order.update(shipped=False)
            messages.success(request, 'Shipping status updated.')
            return redirect('home')
        return render(request, 'payment/shipped_dash.html', {"orders": orders})
    else:
        messages.error(
            request,
            'You do not have permission to access this page.')
        return redirect('home')


def paid_dash(request):
    if request.user.is_authenticated and request.user.is_superuser:
        orders = Order.objects.filter(is_paid=True)
        if request.POST:
            status = request.POST.get('payment_status')
            num = request.POST.get('num')
            order = Order.objects.filter(id=num)
            if status == 'false':
                order.update(
                    is_paid=False,
                    payment_status=Order.PaymentStatus.PENDING,
                    date_paid=None,
                )
                messages.success(request, 'Payment status updated.')
            return redirect('paid_dash')
        return render(request, 'payment/paid_dash.html', {"orders": orders})
    else:
        messages.error(
            request,
            'You do not have permission to access this page.')
        return redirect('home')


def not_paid_dash(request):
    if request.user.is_authenticated and request.user.is_superuser:
        orders = Order.objects.filter(
            payment_status=Order.PaymentStatus.PENDING)
        if request.POST:
            status = request.POST.get('payment_status')
            num = request.POST.get('num')
            order = Order.objects.filter(id=num)
            if status == 'true':
                now = datetime.datetime.now()
                order.update(
                    is_paid=True,
                    payment_status=Order.PaymentStatus.PAID,
                    date_paid=now,
                )
                messages.success(request, 'Payment status updated.')
            return redirect('not_paid_dash')
        return render(request, 'payment/not_paid_dash.html',
                      {"orders": orders})
    else:
        messages.error(
            request,
            'You do not have permission to access this page.')
        return redirect('home')


def process_order(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Please log in to place your order.')
        return redirect('login')

    if request.POST:
        if _create_order_from_current_cart(
                request,
                is_paid=False,
                payment_status=Order.PaymentStatus.PENDING,
                clear_cart=True):
            messages.success(request, 'Order processed successfully!')
            return redirect('home')
        return redirect('cart_summary')
    else:
        messages.error(request, 'Invalid request method.')
        return redirect('home')


def create_checkout_session(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Please log in to continue to checkout.')
        return redirect('login')

    if request.method != 'POST':
        messages.error(request, 'Invalid request method.')
        return redirect('checkout')

    cart = Cart(request)
    cart_products = cart.get_prods()
    quantities = cart.get_quants()
    shipping_info = request.session.get('my_shipping')

    if not cart_products:
        messages.error(request, 'Your cart is empty.')
        return redirect('cart_summary')

    if not shipping_info:
        messages.error(
            request,
            'Shipping details are missing. Please try again.')
        return redirect('checkout')

    if not settings.STRIPE_SECRET_KEY:
        messages.error(
            request,
            'Stripe secret key is missing in environment settings.')
        return redirect('checkout')

    stripe.api_key = settings.STRIPE_SECRET_KEY

    line_items = []
    for product in cart_products:
        quantity = quantities.get(str(product.id))
        if not quantity:
            continue
        unit_price = product.sale_price if product.is_sale else product.price
        amount_cents = int(
            (Decimal(unit_price) * 100).quantize(
                Decimal('1'),
                rounding=ROUND_HALF_UP,
            )
        )
        line_items.append({
            'price_data': {
                'currency': 'usd',
                'product_data': {
                    'name': product.name,
                },
                'unit_amount': amount_cents,
            },
            'quantity': quantity,
        })

    if not line_items:
        messages.error(request, 'No valid line items found in cart.')
        return redirect('cart_summary')

    pending_order = _create_order_from_current_cart(
        request,
        is_paid=False,
        payment_status=Order.PaymentStatus.PENDING,
        clear_cart=False,
    )
    if not pending_order:
        return redirect('checkout')

    success_url = request.build_absolute_uri(
        reverse('payment_success')) + '?session_id={CHECKOUT_SESSION_ID}'
    cancel_url = request.build_absolute_uri(reverse('checkout'))

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='payment',
            line_items=line_items,
            customer_email=shipping_info.get('shipping_email', ''),
            metadata={'order_id': str(pending_order.id)},
            payment_intent_data={
                'metadata': {'order_id': str(pending_order.id)}
            },
            client_reference_id=str(pending_order.id),
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except stripe.error.StripeError:
        pending_order.delete()
        messages.error(
            request,
            'Unable to start Stripe checkout right now. Please try again.')
        return redirect('checkout')

    pending_order.stripe_checkout_session_id = checkout_session.id
    if checkout_session.payment_intent:
        pending_order.stripe_payment_intent_id = str(
            checkout_session.payment_intent)
    pending_order.save(
        update_fields=['stripe_checkout_session_id', 'stripe_payment_intent_id'])

    return redirect(checkout_session.url, code=303)


def checkout(request):
    if not request.user.is_authenticated:
        messages.error(request, 'Please log in to access checkout.')
        return redirect('login')

    cart = Cart(request)
    cart_products = cart.get_prods()
    quantities = cart.get_quants()
    totals = cart.cart_total()
    if request.user.is_authenticated:
        shipping_user = ShippingAddress.objects.filter(
            user=request.user).first()
        shipping_form = ShippingForm(
            request.POST or None,
            instance=shipping_user)
    else:
        shipping_form = ShippingForm(request.POST or None)

    shipping_info = request.session.get('my_shipping')
    if request.method == 'POST' and shipping_form.is_valid():
        shipping_info = shipping_form.cleaned_data
        request.session['my_shipping'] = shipping_info

    return render(request, 'payment/checkout.html', {
        'cart_products': cart_products,
        'quantities': quantities,
        'totals': totals,
        'shipping_form': shipping_form,
        'shipping_info': shipping_info,
    })


def payment_success(request):
    session_id = request.GET.get('session_id')
    if not session_id:
        messages.error(request, 'Missing Stripe session information.')
        return redirect('checkout')

    if request.session.get('stripe_last_session_id') == session_id:
        return render(request, 'payment/payment_success.html', {})

    if not settings.STRIPE_SECRET_KEY:
        messages.error(
            request,
            'Stripe secret key is missing in environment settings.')
        return redirect('checkout')

    stripe.api_key = settings.STRIPE_SECRET_KEY
    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError:
        messages.error(
            request,
            'Unable to verify Stripe payment. Please try again.')
        return redirect('checkout')

    if checkout_session.payment_status != 'paid':
        messages.error(request, 'Payment is not completed yet.')
        return redirect('checkout')

    order = Order.objects.filter(stripe_checkout_session_id=session_id).first()
    if not order:
        metadata = _safe_stripe_attr(checkout_session, 'metadata', {})
        metadata_order_id = _safe_stripe_attr(metadata, 'order_id')
        if metadata_order_id:
            order = Order.objects.filter(id=metadata_order_id).first()

    if order:
        _update_order_payment_state(
            order,
            is_paid=True,
            payment_status=Order.PaymentStatus.PAID,
            stripe_checkout_session_id=session_id,
            stripe_payment_intent_id=str(
                _safe_stripe_attr(checkout_session, 'payment_intent', '') or ''),
        )
    else:
        order = _create_order_from_current_cart(
            request,
            is_paid=True,
            payment_status=Order.PaymentStatus.PAID,
            clear_cart=False,
            stripe_checkout_session_id=session_id,
            stripe_payment_intent_id=str(
                _safe_stripe_attr(checkout_session, 'payment_intent', '') or ''),
        )
        if not order:
            return redirect('cart_summary')

    _clear_cart_after_payment(request)
    email_sent, email_error = _send_payment_confirmation_email(order)
    if not email_sent:
        warning_text = 'Payment completed, but confirmation email could not be sent.'
        if settings.DEBUG and email_error:
            warning_text = f'{warning_text} Reason: {email_error}'
        messages.warning(request, warning_text)
    request.session['stripe_last_session_id'] = session_id
    return render(request, 'payment/payment_success.html', {})


@csrf_exempt
def stripe_webhook(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    if not settings.STRIPE_WEBHOOK_SECRET:
        return HttpResponse(status=500)

    payload = request.body
    signature = request.META.get('HTTP_STRIPE_SIGNATURE')

    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return HttpResponse(status=400)

    event_type = _safe_stripe_attr(event, 'type')
    event_data = _safe_stripe_attr(event, 'data', {})
    data = _safe_stripe_attr(event_data, 'object', {})

    if event_type == 'payment_intent.succeeded':
        payment_intent_id = str(_safe_stripe_attr(data, 'id', '') or '')
        order = _resolve_order_from_stripe_object(data)
        if order:
            _mark_order_paid_idempotent(
                order,
                stripe_payment_intent_id=payment_intent_id,
            )

    elif event_type == 'payment_intent.payment_failed':
        payment_intent_id = str(_safe_stripe_attr(data, 'id', '') or '')
        order = _resolve_order_from_stripe_object(data)
        if order:
            _mark_order_failed_idempotent(
                order,
                stripe_payment_intent_id=payment_intent_id,
            )

    elif event_type == 'checkout.session.completed':
        session_id = _safe_stripe_attr(data, 'id')
        payment_intent_id = str(
            _safe_stripe_attr(
                data,
                'payment_intent',
                '') or '')

        order = None
        metadata = _safe_stripe_attr(data, 'metadata', {})
        metadata_order_id = _safe_stripe_attr(metadata, 'order_id')
        if metadata_order_id:
            order = Order.objects.filter(id=metadata_order_id).first()
        if not order and session_id:
            order = Order.objects.filter(
                stripe_checkout_session_id=session_id).first()

        if order:
            _update_order_payment_state(
                order,
                is_paid=True,
                payment_status=Order.PaymentStatus.PAID,
                stripe_checkout_session_id=session_id,
                stripe_payment_intent_id=payment_intent_id,
            )

    elif event_type in ['checkout.session.async_payment_failed', 'checkout.session.expired']:
        session_id = _safe_stripe_attr(data, 'id')
        order = Order.objects.filter(
            stripe_checkout_session_id=session_id).first()
        if order:
            _update_order_payment_state(
                order,
                is_paid=False,
                payment_status=Order.PaymentStatus.FAILED,
                stripe_checkout_session_id=session_id,
            )

    elif event_type == 'charge.refunded':
        payment_intent_id = str(
            _safe_stripe_attr(
                data,
                'payment_intent',
                '') or '')
        order = Order.objects.filter(
            stripe_payment_intent_id=payment_intent_id).first()
        if order:
            amount = _safe_stripe_attr(data, 'amount', 0)
            amount_refunded = _safe_stripe_attr(data, 'amount_refunded', 0)
            if amount and amount_refunded >= amount:
                refund_status = Order.PaymentStatus.REFUNDED
                is_paid = False
            else:
                refund_status = Order.PaymentStatus.PARTIALLY_REFUNDED
                is_paid = True

            _update_order_payment_state(
                order,
                is_paid=is_paid,
                payment_status=refund_status,
                stripe_payment_intent_id=payment_intent_id,
            )

    return HttpResponse(status=200)
