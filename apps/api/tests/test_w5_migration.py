from pathlib import Path


def test_w5_migration_contains_reproducibility_and_delivery_guards() -> None:
    sql=(Path(__file__).parents[3]/"db/sql/0005_w5_recommendation_alerts.sql").read_text()
    for token in ("user_preference_versions","snapshot_hash","input_hash","definition_hash","alert_channel_preferences","provider_response","next_attempt_at","max_attempts"):
        assert token in sql
