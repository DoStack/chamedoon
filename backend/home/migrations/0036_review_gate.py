import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('home', '0034_ai_usage_counter')]

    operations = [
        migrations.AddField(
            model_name='homesettings',
            name='review_gate_on',
            field=models.BooleanField(default=False, help_text='Settle duplicates and posts with no housing word by rule, with no model call. Run settle_review_backlog once after switching it on. Off: reading and review work as before.'),
        ),
        migrations.CreateModel(
            name='HomeGateSample',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('missed', models.BooleanField(default=False, help_text='The model accepted it: the gate lost a real listing, and the post is now a listing.')),
                ('model_reason', models.CharField(blank=True, help_text='Why the model rejected it too.', max_length=64)),
                ('model', models.CharField(blank=True, max_length=96)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('post', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='gate_sample', to='home.homepost')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
