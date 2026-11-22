from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from core.api_views import HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
]
