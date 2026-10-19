from pathlib import Path

from hackathon_sources.models import IngestionMode, PermissionStatus
from hackathon_sources.registry import load_source_definitions


def test_seed_registry_is_valid_and_safe_by_default() -> None:
    root = Path(__file__).resolve().parents[3]
    sources = load_source_definitions(root / "db/seeds/sources_v0_1.json")
    assert len(sources) >= 20
    keys = {source.key for source in sources}
    assert len(keys) == len(sources)
    devpost = next(source for source in sources if source.key == "devpost")
    assert devpost.ingestion_mode is IngestionMode.DISCOVERY_ONLY
    assert devpost.permission_status is PermissionStatus.RESTRICTED
    assert not devpost.detail_extraction_enabled
