# OER/accounts/backends.py

from django.contrib.auth.backends import ModelBackend
# FIX: We are importing your custom User model directly
from .models import User

class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        try:
            user = User.objects.get(email__iexact=username)
        except User.DoesNotExist:
            return None

        # ✅ Respect is_active and Django auth rules
        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None