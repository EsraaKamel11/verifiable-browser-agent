# src/vba/perceive/elements.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Element:
    target_id: int
    tag: str
    role: str
    name: str          # accessible name
    element_id: str    # the id attribute, or ""
    name_attr: str     # the name attribute, or ""
    input_type: str    # the type attribute, or ""
    is_submit: bool
    selector: str      # resolved by the extractor; never emitted by the model


@dataclass(frozen=True)
class Observation:
    url: str
    epoch: int
    elements: list[Element]
    text: str
    fingerprint: str = ""

    def by_id(self, target_id: int) -> Element:
        for e in self.elements:
            if e.target_id == target_id:
                return e
        raise KeyError("no element with target_id " + str(target_id))


def elements_from_records(records: list[dict]) -> list[Element]:
    return [Element(target_id=i, **r) for i, r in enumerate(records)]
