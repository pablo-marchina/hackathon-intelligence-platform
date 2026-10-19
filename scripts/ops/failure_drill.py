from __future__ import annotations

from hackathon_operations.isolation import SourceAvailability, evaluate_source_failure_isolation


def main() -> int:
    result = evaluate_source_failure_isolation(
        [
            SourceAvailability("major-source", False, 1000),
            SourceAvailability("secondary-a", True, 400),
            SourceAvailability("secondary-b", True, 220),
        ]
    )
    print(result)
    return 0 if result.product_available else 1


if __name__ == "__main__":
    raise SystemExit(main())
