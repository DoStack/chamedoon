import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """New tables only. HomeSaved's foreign key takes a brief lock on the listing table that reads pass through."""

    dependencies = [
        ('home', '0025_snapshot_state'),
        ('users', '0003_mark_imported_request_owners'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeArea',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('country', models.CharField(max_length=2)),
                ('city', models.CharField(help_text='The metro: the city of its channel.', max_length=64)),
                ('canonical', models.CharField(help_text='Plain lower-case Latin name, as home.places.fold writes it.', max_length=64)),
                ('fa', models.CharField(max_length=64, verbose_name='Persian name')),
                ('group', models.CharField(blank=True, help_text='The heading in the all-areas sheet. Blank: no heading.', max_length=32)),
                ('aliases', models.TextField(blank=True, help_text='Other names, comma separated: spellings, suburbs, Toronto intersections.')),
                ('lat', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True)),
                ('lng', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True)),
                ('rank', models.PositiveSmallIntegerField(default=0, help_text='Order among areas with the same count. Lower comes first.')),
            ],
            options={
                'ordering': ['country', 'city', 'rank', 'canonical'],
                'constraints': [models.UniqueConstraint(fields=('country', 'city', 'canonical'), name='home_area_country_city_canonical')],
            },
        ),
        migrations.CreateModel(
            name='HomeSaved',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('note', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='saved_by', to='home.homelisting')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='home_saved', to='users.user')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'constraints': [models.UniqueConstraint(fields=('user', 'listing'), name='home_saved_user_listing')],
            },
        ),
    ]
