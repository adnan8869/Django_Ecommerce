from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payment', '0006_order_is_paid_order_date_paid'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('paid', 'Paid'),
                    ('failed', 'Failed'),
                    ('refunded', 'Refunded'),
                    ('partially_refunded', 'Partially Refunded'),
                ],
                default='pending',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='stripe_checkout_session_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True),
        ),
        migrations.AddField(
            model_name='order',
            name='stripe_payment_intent_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True),
        ),
    ]
