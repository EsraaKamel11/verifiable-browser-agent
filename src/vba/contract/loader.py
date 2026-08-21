import yaml

from .schema import Contract


def load_contract(path: str) -> Contract:
    with open(path, encoding="utf-8") as fh:
        return Contract.model_validate(yaml.safe_load(fh))
