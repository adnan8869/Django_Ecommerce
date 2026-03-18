from django.contrib import admin

from .models import Category, Customer, Order, Product

admin.site.register(Category)
admin.site.register(Customer)
admin.site.register(Product)
admin.site.register(Order)
