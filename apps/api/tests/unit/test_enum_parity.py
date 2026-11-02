import pytest

import engleks_api.domain.text_processing as domain
import engleks_contracts as wire


@pytest.mark.parametrize(
    ("domain_enum", "wire_enum"),
    [(domain.Mode, wire.Mode), (domain.Language, wire.Language)],
)
def test_domain_and_contract_enums_match(domain_enum, wire_enum):
    assert [m.value for m in domain_enum] == [m.value for m in wire_enum]
