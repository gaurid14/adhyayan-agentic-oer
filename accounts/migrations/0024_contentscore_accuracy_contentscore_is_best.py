from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0023_contributornote"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE accounts_contentscore
                            ADD COLUMN IF NOT EXISTS accuracy double precision NULL;
                        ALTER TABLE accounts_contentscore
                            ADD COLUMN IF NOT EXISTS is_best boolean NOT NULL DEFAULT false;
                    """,
                    reverse_sql="""
                        ALTER TABLE accounts_contentscore DROP COLUMN IF EXISTS is_best;
                        ALTER TABLE accounts_contentscore DROP COLUMN IF EXISTS accuracy;
                    """,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="contentscore",
                    name="accuracy",
                    field=models.FloatField(blank=True, null=True),
                ),
                migrations.AddField(
                    model_name="contentscore",
                    name="is_best",
                    field=models.BooleanField(default=False),
                ),
            ],
        )
    ]
