from django.shortcuts import render, redirect
from .models import Product, Category, Profile
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .forms import SignUpForm, UpdateUserForm, ChangePasswordForm, UserInfoForm
from payment.forms import ShippingForm
from payment.models import ShippingAddress
from django import forms
from django.db.models import Q
import json
from cart.cart import Cart


def search(request):
    if request.method == 'POST':
        searched = request.POST['searched']
        searched = Product.objects.filter(
            Q(name__icontains=searched) | Q(description__icontains=searched))
        if not searched:
            messages.error(request, 'No products found matching your search.')
            return render(request, 'search.html', {})
        else:
            return render(request, 'search.html', {'searched': searched})
    else:
        return render(request, 'search.html', {})


def update_info(request):
    if request.user.is_authenticated:
        current_user = Profile.objects.get(user__id=request.user.id)
        shipping_user = ShippingAddress.objects.filter(
            user=request.user).first()
        form = UserInfoForm(request.POST or None, instance=current_user)
        shipping_form = ShippingForm(
            request.POST or None,
            instance=shipping_user)
        if form.is_valid() and shipping_form.is_valid():
            form.save()
            shipping_address = shipping_form.save(commit=False)
            shipping_address.user = request.user
            shipping_address.save()
            messages.success(request, 'Your Info has been updated.')
            return redirect('home')
        return render(request, 'update_info.html', {
                      'form': form, 'shipping_form': shipping_form})
    else:
        messages.error(
            request,
            'You must be logged in to update your profile.')
        return redirect('login')


def update_password(request):
    if request.user.is_authenticated:
        current_user = request.user
        if request.method == 'POST':
            form = ChangePasswordForm(current_user, request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, 'Your password has been updated.')
                login(request, current_user)
                return redirect('update_user')
            else:
                for error in list(form.errors.values()):
                    messages.error(request, error)
                    return redirect('update_password')
        else:
            form = ChangePasswordForm(current_user)
            return render(request, 'update_password.html', {'form': form})
    else:
        messages.error(
            request,
            'You must be logged in to change your password.')
        return redirect('login')


def update_user(request):
    if request.user.is_authenticated:
        current_user = User.objects.get(id=request.user.id)
        user_form = UpdateUserForm(request.POST or None, instance=current_user)
        if user_form.is_valid():
            user_form.save()
            login(request, current_user)
            messages.success(request, 'Your profile has been updated.')
            return redirect('home')
        return render(request, 'update_user.html', {'user_form': user_form})
    else:
        messages.error(
            request,
            'You must be logged in to update your profile.')
        return redirect('login')


def category_summary(request):
    category = Category.objects.all()
    return render(request, 'category_summary.html', {'categories': category})


def category(request, foo):
    foo = foo.replace('-', ' ')
    try:
        category = Category.objects.get(name=foo)
        products = Product.objects.filter(category=category)
        return render(request, 'category.html', {
                      'category': category, 'products': products})
    except Category.DoesNotExist:
        messages.error(request, 'Category does not exist.')
        return redirect('home')


def product(request, pk):
    product = Product.objects.get(id=pk)
    return render(request, 'product.html', {'product': product})


def home(request):
    products = Product.objects.all()
    return render(request, 'home.html', {'products': products})


def about(request):
    return render(request, 'about.html', {})


def login_user(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            # Update the user's cart with any saved items
            current_user = Profile.objects.get(user__id=request.user.id)
            saved_cart = current_user.old_cart
            # Convert the saved cart string back to a dictionary
            if saved_cart:
                converted_cart = json.loads(saved_cart)
                cart = Cart(request)
                for key, value in converted_cart.items():
                    cart.db_add(product=key, quantity=value)

            messages.success(request, 'You have been logged in.')
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password.')
            return redirect('login')
    else:
        return render(request, 'login.html', {})


def logout_user(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('home')


def register_user(request):
    form = SignUpForm()
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data['username']
            password = form.cleaned_data['password1']
            user = authenticate(username=username, password=password)
            login(request, user)
            messages.success(
                request, 'User created, please fill your profile information.')
            return redirect('update_info')
        else:
            messages.error(
                request, 'Unsuccessful registration. Invalid information.')
            return redirect('register')
    else:
        return render(request, 'register.html', {'form': form})
