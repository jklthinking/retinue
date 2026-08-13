import pytest

from core.protocol.org import initialize, validate_org
from core.protocol.task import ProtocolError


def sample_org():
    return {
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering", "lead": "coder-1"}],
        "agents": [
            {"id": "coder-1", "dept": "eng", "runtime": "runtime-a", "node": "node-1"}
        ],
        "nodes": [{"id": "node-1"}],
    }


def test_valid_org():
    validate_org(sample_org())


def test_global_ids_are_unique():
    data = sample_org()
    data["nodes"][0]["id"] = "eng"
    with pytest.raises(ProtocolError, match="globally unique"):
        validate_org(data)


def test_duplicate_ids_within_one_entity_type_are_rejected():
    data = sample_org()
    data["departments"].append({"id": "eng", "name": "Duplicate"})
    with pytest.raises(ProtocolError, match="globally unique"):
        validate_org(data)


def test_non_mapping_entity_is_rejected():
    data = sample_org()
    data["nodes"].append("node-2")
    with pytest.raises(ProtocolError, match="must be a mapping"):
        validate_org(data)


def test_non_string_id_is_rejected():
    data = sample_org()
    data["nodes"][0]["id"] = 7
    with pytest.raises(ProtocolError, match="valid id"):
        validate_org(data)


def test_agent_references_existing_department():
    data = sample_org()
    data["agents"][0]["dept"] = "sales"
    with pytest.raises(ProtocolError, match="unknown department"):
        validate_org(data)


def test_lead_belongs_to_department():
    data = sample_org()
    data["departments"].append({"id": "ops", "name": "Operations", "lead": "coder-1"})
    with pytest.raises(ProtocolError, match="must be an agent"):
        validate_org(data)


def test_init_creates_layout_and_refuses_overwrite(tmp_path):
    target = initialize(tmp_path / "demo", "acme-inc")
    assert target.is_file()
    assert (target.parent / "tasks").is_dir()
    assert (target.parent / "nodes").is_dir()
    with pytest.raises(ProtocolError, match="refusing to overwrite"):
        initialize(target.parent, "acme-inc")
