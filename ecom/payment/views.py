from django.shortcuts import redirect, render
from cart.cart import Cart
from payment.forms import ShippingForm, PaymentForm
from payment.models import ShippingAddress, Order, OrderItem
from django.contrib.auth.models import User
from django.contrib import messages
from store.models import Product
import datetime


def orders(request, pk):
    if request.user.is_authenticated and request.user.is_superuser:
        order = Order.objects.get(id=pk)
        items = OrderItem.objects.filter(order_id=pk).select_related('product')
        if request.POST:
            status = request.POST.get('shipping_status')
            if status == 'true':
                order = Order.objects.filter(id=pk)
                now = datetime.datetime.now()
                order.update(shipped=True, date_shipped=now)
            else:
                order = Order.objects.filter(id=pk)
                order.update(shipped=False)
            messages.success(request, 'Shipping status updated.')
            return redirect('home')
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


def process_order(request):
    if request.POST:
        cart = Cart(request)
        cart_products = cart.get_prods()
        quantities = cart.get_quants()
        totals = cart.cart_total()
        if not cart_products:
            messages.error(request, 'Your cart is empty.')
            return redirect('cart_summary')
        # Get billing from the last page
        payment_form = PaymentForm(request.POST or None)
        # Get shipping info from session
        my_shipping = request.session.get('my_shipping')
        # Order Info
        full_name = my_shipping['shipping_full_name']
        email = my_shipping['shipping_email']
        shipping_address = f"{my_shipping['shipping_address1']}\n{my_shipping['shipping_address2']}\n{my_shipping['shipping_city']}\n{my_shipping['shipping_state']}\n{my_shipping['shipping_zip_code']}"
        amount_paid = totals
        # Create an order
        if request.user.is_authenticated:
            user = request.user
            create_order = Order(
                user=user,
                full_name=full_name,
                email=email,
                shipping_address=shipping_address,
                amount_paid=amount_paid)
            create_order.save()
        # Add orders items
        # Order id, Product id, quantity, price
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
                        price=price)
                    create_order_item.save()
                    created_items += 1

            if created_items == 0:
                create_order.delete()
                messages.error(
                    request, 'No order items were created. Please try checkout again.')
                return redirect('cart_summary')
            # Delete cart
            request.session.pop('session_key', None)

            messages.success(request, 'Order processed successfully!')
            return redirect('home')
        else:
            create_order = Order(
                full_name=full_name,
                email=email,
                shipping_address=shipping_address,
                amount_paid=amount_paid)
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
                        price=price)
                    create_order_item.save()
                    created_items += 1

            if created_items == 0:
                create_order.delete()
                messages.error(
                    request, 'No order items were created. Please try checkout again.')
                return redirect('cart_summary')
            # Delete cart
            request.session.pop('session_key', None)
            messages.success(request, 'Order processed successfully!')
            return redirect('home')
    else:
        messages.error(request, 'Invalid request method.')
        return redirect('home')


def billing_info(request):
    if request.POST:
        cart = Cart(request)
        cart_products = cart.get_prods()
        quantities = cart.get_quants()
        totals = cart.cart_total()

        my_shipping = request.POST
        request.session['my_shipping'] = my_shipping

        if request.user.is_authenticated:
            billing_form = PaymentForm()
            return render(request, 'payment/billing_info.html', {
                "cart_products": cart_products, "quantities": quantities, "totals": totals, "shipping_info": request.POST, "billing_form": billing_form})

        else:
            billing_form = PaymentForm()
            return render(request, 'payment/billing_info.html', {
                "cart_products": cart_products, "quantities": quantities, "totals": totals, "shipping_info": request.POST, "billing_form": billing_form})

        shipping_form = request.POST
        return render(request, 'payment/billing_info.html', {
            "cart_products": cart_products, "quantities": quantities, "totals": totals, "shipping_form": shipping_form})
    else:
        messages.error(request, 'Invalid request method.')
        return redirect('home')


def checkout(request):
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
        return render(request, 'payment/checkout.html', {
            "cart_products": cart_products, "quantities": quantities, "totals": totals, "shipping_form": shipping_form})
    else:
        shipping_form = ShippingForm(
            request.POST or None,)
        return render(request, 'payment/checkout.html', {
            "cart_products": cart_products, "quantities": quantities, "totals": totals, "shipping_form": shipping_form})


def payment_success(request):
    return render(request, 'payment/payment_success.html', {})
