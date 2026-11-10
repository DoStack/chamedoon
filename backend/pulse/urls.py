from django.urls import path

from pulse import views

app_name = "pulse"
urlpatterns = [
    path("admin/pulse/", views.overview, name="overview"),
    path("admin/pulse/acquisition/", views.acquisition, name="acquisition"),
    path("admin/pulse/acquisition/<str:city>/", views.acquisition_city, name="acquisition_city"),
    path("admin/pulse/users/", views.users_page, name="users"),
    path("admin/pulse/users/<int:user_id>/", views.user_detail, name="user"),
    path("admin/pulse/flows/", views.flows, name="flows"),
    path("admin/pulse/outreach/", views.outreach, name="outreach"),
    path("admin/pulse/pipeline/", views.pipeline, name="pipeline"),
    path("admin/pulse/posts/", views.posts, name="posts"),
    path("admin/pulse/posts/<int:post_id>/", views.post_detail, name="post"),
    path("admin/pulse/doubts/", views.doubts, name="doubts"),
    path("admin/pulse/listings/", views.listings, name="listings"),
    path("admin/pulse/health/", views.health, name="health"),
]
