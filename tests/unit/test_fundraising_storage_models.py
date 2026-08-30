from disclosure_agent.storage.fundraising_models import (
    FundraisingEventRow,
    FundraisingEventSourceRow,
)


def test_fundraising_tables_are_declared_with_expected_primary_keys() -> None:
    assert FundraisingEventRow.__tablename__ == "fundraising_events"
    assert FundraisingEventSourceRow.__tablename__ == "fundraising_event_sources"
    assert [column.name for column in FundraisingEventRow.__table__.primary_key] == ["event_id"]
    assert [column.name for column in FundraisingEventSourceRow.__table__.primary_key] == [
        "source_id"
    ]


def test_fundraising_source_rows_reference_canonical_events() -> None:
    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in FundraisingEventSourceRow.__table__.c.event_id.foreign_keys
    }
    assert foreign_keys == {"fundraising_events.event_id"}
