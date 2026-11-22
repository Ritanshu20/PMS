from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User


@admin.register(User)
class PMSUserAdmin(UserAdmin):
    fieldsets = (*UserAdmin.fieldsets, ("PMS", {"fields": ("display_name",)}))
    list_display = ("username", "email", "display_name", "is_staff")
