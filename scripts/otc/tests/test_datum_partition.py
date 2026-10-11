"""Test vectors for the sealed datums partition rule (datums spec §5.1, slot DT0b).

The expected values were computed outside Python, with
`printf '%s' "otc-datums-holdout-v1|<source>|<id>" | openssl dgst -sha256 -r` (first 16 hex digits)
and `bc` (to decimal, then mod 3), so a wrong byte order, prefix or slice in the code fails here.
"""
import pytest

from otc_pipeline.datum_partition import PARTITIONS, partition, partition_key

VECTORS = [
    # source, id, first 8 bytes of h (hex), as unsigned big-endian int, partition
    ("noaa", "8443970", "6132e29ee9a14fcf", 7003909542666915791, "reserve"),
    ("noaa", "9447130", "787975b1a9c6d063", 8681099162651381859, "tuning"),
    ("noaa", "1611400", "cf0edc94dcdec149", 14920105147443888457, "holdout"),
    ("noaa", "8724580", "181bf6dc4b2aca67", 1737253507295791719, "tuning"),
    ("kartverket", "BGO", "eb8ba910244bcdb6", 16972845507469364662, "holdout"),
    ("kartverket", "OSL", "422aff5272358324", 4767903885093602084, "reserve"),
    ("kartverket", "TRD", "6bb93e587525f642", 7762303982393226818, "reserve"),
    ("linz", "port_chalmers", "50e6caae8811e4a5", 5829569618633680037, "reserve"),
    ("linz", "auckland", "aac925748e6ebbf7", 12306408639302646775, "holdout"),
    ("pegelonline", "aad49293-242a-43ad-a8b1-e91d7792c4b2", "4a31162cafa8c278", 5346078613846934136, "tuning"),
]


@pytest.mark.parametrize("source,id,hex8,key,expected", VECTORS)
def test_vector(source, id, hex8, key, expected):
    assert int(hex8, 16) == key
    assert partition_key(source, id) == key
    assert partition(source, id) == expected
    assert PARTITIONS[key % 3] == expected


def test_vectors_cover_every_partition():
    assert {v[4] for v in VECTORS} == set(PARTITIONS) == {"tuning", "holdout", "reserve"}


@pytest.mark.parametrize("source,id", [
    ("NOAA", "8443970"), ("noaa ", "8443970"), ("ioc", "8443970"), ("", "8443970"),
    ("noaa", "844397"), ("noaa", "08443970"), ("noaa", "TEC4635"), ("noaa", " 8443970"),
    ("kartverket", "bgo"), ("kartverket", "BG0"), ("kartverket", "BGOX"),
    ("linz", "Port Chalmers"), ("linz", "port chalmers"), ("linz", "port_chalmers "), ("linz", "Port_Chalmers"),
    ("pegelonline", "AAD49293-242A-43AD-A8B1-E91D7792C4B2"), ("pegelonline", "aad49293242a43ada8b1e91d7792c4b2"),
    ("pegelonline", "{aad49293-242a-43ad-a8b1-e91d7792c4b2}"),
])
def test_rule_refuses_unknown_source_or_unnormalised_id(source, id):
    # The rule hashes (source, id) exactly as given, so it refuses anything not already canonical
    # instead of silently returning another partition.
    with pytest.raises(ValueError):
        partition_key(source, id)
    with pytest.raises(ValueError):
        partition(source, id)


# --- the one-partition loader ----------------------------------------------------------------------------

from otc_pipeline.datum_partition import SOURCES, load  # noqa: E402


@pytest.mark.parametrize("source", SOURCES)
def test_loader_returns_one_partition_and_every_sealed_id_is_canonical(source):
    lists = {p: load("partitions", p, source, sealed_run=True) for p in PARTITIONS}
    assert all(lists.values())
    seen = set()
    for p, ids in lists.items():
        assert not seen & set(ids)
        seen |= set(ids)
        for i in ids:
            assert partition(source, i) == p          # validates the id, and the list is the rule's
    assert load("partitions", "tuning", source) == lists["tuning"]


def test_loader_t1_lists():
    for p in PARTITIONS:
        st = load("t1", p, sealed_run=True)
        assert len(st) == 20
        assert all(partition("noaa", s["id"]) == p for s in st)
    assert load("t1", "tuning") == load("t1", "tuning", sealed_run=True)


@pytest.mark.parametrize("name", ["holdout", "reserve"])
def test_loader_refuses_holdout_and_reserve_outside_a_sealed_run(name):
    with pytest.raises(PermissionError):
        load("partitions", name, "noaa")
    with pytest.raises(PermissionError):
        load("t1", name)


@pytest.mark.parametrize("name", ["Tuning", "tuning ", "all", "", None, 0])
def test_loader_needs_an_explicit_partition_name(name):
    with pytest.raises(ValueError):
        load("partitions", name, "noaa", sealed_run=True)


def test_loader_refuses_bad_kind_or_source():
    with pytest.raises(ValueError):
        load("partition", "tuning", "noaa")
    with pytest.raises(ValueError):
        load("partitions", "tuning", "NOAA")
    with pytest.raises(ValueError):
        load("partitions", "tuning")          # no source
    with pytest.raises(ValueError):
        load("t1", "tuning", "noaa")          # T1 is NOAA only and takes no source


def test_loader_refuses_a_changed_seal_file(tmp_path):
    import shutil
    from otc_pipeline import datum_partition as dp
    for f in dp.SEALED:
        shutil.copy2(dp.DATA_DIR / f, tmp_path / f)
    assert load("partitions", "tuning", "noaa", data_dir=tmp_path) == load("partitions", "tuning", "noaa")
    (name,) = [f for f in dp.SEALED if f.startswith("partitions")]
    p = tmp_path / name
    p.write_bytes(p.read_bytes().replace(b'"8443970"', b'"8443971"', 1))
    with pytest.raises(ValueError, match="SHA-256"):
        load("partitions", "tuning", "noaa", data_dir=tmp_path)
