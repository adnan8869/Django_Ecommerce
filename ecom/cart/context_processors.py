from .cart import Cart
# Make sure cart is available on all parts of the site

def cart(request):
    return {'cart': Cart(request)}  # retur the default data from the cart class