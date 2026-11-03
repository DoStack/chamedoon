from django.apps import AppConfig


class HomeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "home"
    label = "home"
    verbose_name = "Chamedoon Home"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from home.models import HomeArea, HomeChannel

        def areas_changed(**kwargs):
            from home.areas import mark_areas_changed

            mark_areas_changed()

        for model in (HomeArea, HomeChannel):
            post_save.connect(areas_changed, sender=model, weak=False, dispatch_uid=f"home_areas_changed_save_{model.__name__}")
            post_delete.connect(areas_changed, sender=model, weak=False, dispatch_uid=f"home_areas_changed_delete_{model.__name__}")
