from django.urls import path, re_path

from home_api import views

urlpatterns = [
    path("auth/exchange/", views.ExchangeView.as_view(), name="v1-auth-exchange"),
    path("me/prefs/", views.PrefsView.as_view(), name="v1-prefs"),
    path("me/waitlist/", views.WaitlistView.as_view(), name="v1-waitlist"),
    path("me/listings/<int:pk>/", views.MeListingView.as_view(), name="v1-me-listing"),
    path("me/listings/<int:pk>/contact/", views.ContactView.as_view(), name="v1-listing-contact"),
    path("public/meta/", views.MetaView.as_view(), name="v1-meta"),
    path("public/cities/", views.CitiesView.as_view(), name="v1-cities"),
    path("public/listings/<int:pk>/", views.PublicListingView.as_view(), name="v1-listing"),
    path("public/deeplink/", views.DeepLinkView.as_view(), name="v1-deeplink"),
    path("public/feed/", views.FeedView.as_view(), name="v1-feed"),
    re_path(r"^.*$", views.NotFoundView.as_view(), name="v1-not-found"),
]
