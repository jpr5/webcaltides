"""The source registry (manifest §2.4, GATE-ADP).

EXPECTED names all 16 sources of the first release and what each adapter emits. SOURCES maps a
source to its adapter class; each adapter slot adds exactly one line to it. The combined build
(otc_pipeline.build_otc, C2a) includes the sources of combine.AGENCY_SOURCES; D11 for this release
(owner-delegated decision, 2026-10-11): the release passes when every INCLUDED source passes its
canary. The sources still missing (`missing_sources()`) join later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from otc_pipeline.adapters.dmi import DmiAdapter
from otc_pipeline.adapters.fmi import FmiAdapter
from otc_pipeline.adapters.jma import JmaAdapter
from otc_pipeline.adapters.kartverket import KartverketAdapter
from otc_pipeline.adapters.linz import LinzAdapter
from otc_pipeline.adapters.mi import MarineInstituteAdapter
from otc_pipeline.adapters.noaa import NoaaAdapter
from otc_pipeline.adapters.noaa_currents import NoaaCurrentsAdapter
from otc_pipeline.adapters.pegelonline import PegelonlineAdapter
from otc_pipeline.adapters.rws import RwsAdapter
from otc_pipeline.adapters.shom_refmar import ShomRefmarAdapter
from otc_pipeline.adapters.smhi import SmhiAdapter


@dataclass(frozen=True)
class SourceSpec:
    source_type: Literal["official", "gauge", "model"]
    quantity: Literal["water_level", "current"]
    output: Literal["sets", "records"]   # records: GaugeRecords for the fit pipeline
    slot: str                            # the manifest slot that builds the adapter


EXPECTED: dict[str, SourceSpec] = {
    # official constants
    "noaa": SourceSpec("official", "water_level", "sets", "A-NOAA"),
    "noaa-currents": SourceSpec("official", "current", "sets", "A-NOAAC"),
    "kartverket": SourceSpec("official", "water_level", "sets", "A-KV"),
    "linz": SourceSpec("official", "water_level", "sets", "A-LINZ"),
    "jma": SourceSpec("official", "water_level", "sets", "A-JMA-a"),
    # gauge observations (fit by the GESLA pipeline)
    "pegelonline": SourceSpec("gauge", "water_level", "records", "A-PEG-a"),
    "rws": SourceSpec("gauge", "water_level", "records", "A-RWS-a"),
    "marine-institute": SourceSpec("gauge", "water_level", "records", "A-MI-a"),
    "dmi": SourceSpec("gauge", "water_level", "records", "A-DMI-a"),
    "smhi": SourceSpec("gauge", "water_level", "records", "A-SMHI-a"),
    "fmi": SourceSpec("gauge", "water_level", "records", "A-FMI-a"),
    "uhslc-fd": SourceSpec("gauge", "water_level", "records", "A-UH-a"),
    "shom-refmar": SourceSpec("gauge", "water_level", "records", "A-SHOM-a"),
    # models
    "eot20": SourceSpec("model", "water_level", "sets", "A-EOT"),
    "fes2022": SourceSpec("model", "water_level", "sets", "A-FES"),
    # the GESLA fit build output
    "gesla-fit": SourceSpec("gauge", "water_level", "sets", "A-GES"),
}

# One line per adapter, added in its own slot, e.g.
#     "noaa": NoaaAdapter,
SOURCES: dict[str, type] = {
    "noaa": NoaaAdapter,
    "noaa-currents": NoaaCurrentsAdapter,
    "kartverket": KartverketAdapter,
    "linz": LinzAdapter,
    "jma": JmaAdapter,
    "pegelonline": PegelonlineAdapter,
    "marine-institute": MarineInstituteAdapter,
    "dmi": DmiAdapter,
    "fmi": FmiAdapter,
    "rws": RwsAdapter,
    "shom-refmar": ShomRefmarAdapter,
    "smhi": SmhiAdapter,
}


def missing_sources() -> list[str]:
    """Sources in EXPECTED with no registered adapter yet."""
    return sorted(set(EXPECTED) - set(SOURCES))


def registry_errors() -> list[str]:
    """Registered adapters whose key or attributes disagree with EXPECTED. Attributes are read
    from an instance (an adapter may set them in __init__)."""
    from otc_pipeline.adapter import Adapter
    errors = []
    for key, cls in sorted(SOURCES.items()):
        spec = EXPECTED.get(key)
        if spec is None:
            errors.append(f"SOURCES[{key!r}]: {key!r} is not one of the {len(EXPECTED)} expected sources")
            continue
        try:
            adapter = cls()
        except Exception as e:  # noqa: BLE001 - reported as a registry error
            errors.append(f"SOURCES[{key!r}]: {getattr(cls, '__name__', cls)!r}() raised "
                          f"{type(e).__name__}: {e}")
            continue
        for attr, want in (("source", key), ("source_type", spec.source_type), ("quantity", spec.quantity)):
            got = getattr(adapter, attr, None)
            if got != want:
                errors.append(f"SOURCES[{key!r}]: {attr} {got!r} differs from the registry ({want!r})")
        v = getattr(adapter, "adapter_version", None)
        if not isinstance(v, str) or not v:
            errors.append(f"SOURCES[{key!r}]: adapter_version {v!r} must be a non-empty string")
        sample = getattr(adapter, "contract_sample", None)
        if not isinstance(sample, tuple) or not sample or not all(isinstance(s, str) and s for s in sample):
            errors.append(f"SOURCES[{key!r}]: contract_sample {sample!r} must be a non-empty tuple of ids")
        if not isinstance(adapter, Adapter):
            errors.append(f"SOURCES[{key!r}]: {type(adapter).__name__} does not implement the Adapter protocol")
    return errors
