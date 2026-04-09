import re

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model


class NoFormSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Auto-create a unique username so social login does not show signup form."""

    def is_auto_signup_allowed(self, request, sociallogin):
        return True

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        email = (
            (getattr(sociallogin.user, 'email', '') or '')
            or (sociallogin.account.extra_data.get('email', '') if sociallogin.account else '')
        ).strip()

        if not email:
            return

        user_model = get_user_model()
        existing_user = user_model.objects.filter(email__iexact=email).first()
        if existing_user:
            sociallogin.connect(request, existing_user)

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)

        if getattr(user, 'username', ''):
            return user

        email = (getattr(user, 'email', '') or data.get('email') or '').strip()
        base = email.split('@')[0] if email else 'user'
        # Keep only safe username characters.
        base = re.sub(r'[^a-zA-Z0-9_]+', '', base) or 'user'

        user_model = get_user_model()
        username = base[:150]
        index = 1

        while user_model.objects.filter(username=username).exists():
            suffix = str(index)
            username = f"{base[:150 - len(suffix)]}{suffix}"
            index += 1

        user.username = username
        return user
