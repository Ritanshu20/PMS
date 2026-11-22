from rest_framework import serializers

from accounts.models import User


class RegisterSerializer(serializers.Serializer):
    username = serializers.RegexField(r"^[A-Za-z0-9_.-]{3,150}$", max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    display_name = serializers.CharField(max_length=150, required=False, allow_blank=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "display_name"]
        read_only_fields = fields


class PublicUserSerializer(serializers.ModelSerializer):
    """Minimal user info shown to other project members."""

    class Meta:
        model = User
        fields = ["id", "username", "display_name"]
        read_only_fields = fields
