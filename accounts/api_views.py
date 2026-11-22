from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts import services
from accounts.serializers import LogoutSerializer, RegisterSerializer, UserSerializer
from core.schema import ERROR_RESPONSE


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(request=RegisterSerializer, responses={201: UserSerializer, 400: ERROR_RESPONSE})
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.register_user(**serializer.validated_data)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    throttle_scope = "auth"


class RefreshView(TokenRefreshView):
    throttle_scope = "auth"


class LogoutView(APIView):
    """Blacklists the given refresh token. The short-lived access token simply expires."""

    throttle_scope = "auth"

    @extend_schema(request=LogoutSerializer, responses={204: None, 400: ERROR_RESPONSE})
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.logout_user(refresh_token=serializer.validated_data["refresh"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    @extend_schema(responses=UserSerializer)
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)
