from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("courier", "0006_courier_board_knobs"),
    ]

    operations = [
        migrations.AddField(
            model_name="couriersettings",
            name="v4_route_bar",
            field=models.BooleanField(default=False, help_text="Mini App list: sticky route bar with swap and the side switch (design v4)."),
        ),
        migrations.AddField(
            model_name="couriersettings",
            name="v4_filter_sheet",
            field=models.BooleanField(default=False, help_text="Mini App list: filter sheet with time, what it carries and size, live counts and chips (design v4)."),
        ),
        migrations.AddField(
            model_name="couriersettings",
            name="v4_smart_search",
            field=models.BooleanField(default=False, help_text="Mini App: one-line smart search parsed by rules, no AI model (design v4)."),
        ),
        migrations.AddField(
            model_name="couriersettings",
            name="v4_connections",
            field=models.BooleanField(default=False, help_text="Mini App account: «ارتباط‌ها» page listing intros and matches (design v4)."),
        ),
        migrations.AddField(
            model_name="couriersettings",
            name="v4_report",
            field=models.BooleanField(default=False, help_text="Mini App request page: «گزارش» link that opens a REPORT_USER ticket (design v4)."),
        ),
    ]
