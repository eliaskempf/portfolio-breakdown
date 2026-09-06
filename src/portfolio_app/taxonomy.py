"""Generic named taxonomies represented as asset-to-path memberships."""

from pathlib import Path
from typing import TypeAlias

import yaml

from portfolio_app.holdings import DataError

TaxonomyPath: TypeAlias = tuple[str, ...]
Classifications: TypeAlias = dict[str, dict[str, tuple[TaxonomyPath, ...]]]
UNCLASSIFIED: TaxonomyPath = ("Unclassified",)


def load_classifications(path: Path) -> Classifications:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataError(f"Cannot read classifications YAML {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DataError("Classifications YAML must map asset IDs to classifications.")
    result: Classifications = {}
    for asset_id, entry in raw.items():
        if not isinstance(asset_id, str) or not asset_id.strip() or not isinstance(entry, dict):
            raise DataError("Each classification entry needs a string asset ID and a mapping.")
        if set(entry) - {"classifications"}:
            raise DataError(f"{asset_id}: expected a classifications mapping.")
        taxonomies = entry.get("classifications", {})
        if not isinstance(taxonomies, dict):
            raise DataError(f"{asset_id}: classifications must be a mapping.")
        result[asset_id] = {}
        for name, paths in taxonomies.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(paths, list):
                raise DataError(f"{asset_id}: each named taxonomy must contain a list of paths.")
            clean_paths = []
            for path_value in paths:
                if not isinstance(path_value, list) or not path_value or any(
                    not isinstance(label, str) or not label.strip() for label in path_value
                ):
                    raise DataError(f"{asset_id}/{name}: paths must be nonempty lists of labels; weighted paths are not supported yet.")
                clean_paths.append(tuple(label.strip() for label in path_value))
            result[asset_id][name] = tuple(dict.fromkeys(clean_paths))
    return result


def taxonomy_names(classifications: Classifications) -> list[str]:
    return sorted({name for entry in classifications.values() for name in entry})


def paths_for(classifications: Classifications, asset_id: str, taxonomy: str) -> tuple[TaxonomyPath, ...]:
    return classifications.get(asset_id, {}).get(taxonomy) or (UNCLASSIFIED,)


def is_descendant(path: TaxonomyPath, root: TaxonomyPath) -> bool:
    return path[: len(root)] == root


def branches(classifications: Classifications, asset_ids: list[str], taxonomy: str) -> list[TaxonomyPath]:
    return sorted({
        path[:depth]
        for asset_id in asset_ids
        for path in paths_for(classifications, asset_id, taxonomy)
        for depth in range(1, len(path) + 1)
    })


def describe(classifications: Classifications, asset_id: str, taxonomy: str) -> str:
    return "; ".join(" > ".join(path) for path in paths_for(classifications, asset_id, taxonomy))
