from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any

import yaml

from .errors import ContractError


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def validate_identifier(value: str, label: str = "identifier") -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ContractError(f"invalid {label}: {value!r}")
    return value


def quote_identifier(value: str) -> str:
    validate_identifier(value)
    return f'"{value}"'


@dataclass(frozen=True)
class Target:
    catalog: str
    namespace: str
    table: str

    @property
    def dotted(self) -> str:
        return f"{self.catalog}.{self.namespace}.{self.table}"


@dataclass(frozen=True)
class UpdatePolicy:
    strategy: str = "create"
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contract:
    dataset_id: str
    path: Path
    digest: str
    kind: str
    owner: str
    description: str
    target: Target
    update: UpdatePolicy
    quality: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    transform: dict[str, Any] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()

    @property
    def source_path(self) -> Path | None:
        raw = self.source.get("path") or self.source.get("archived_path")
        return Path(raw).expanduser() if raw else None

    @property
    def source_format(self) -> str:
        return str(self.source.get("format", "parquet")).lower()


class ContractLoader:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._contracts: dict[str, Contract] | None = None

    def all(self) -> dict[str, Contract]:
        if self._contracts is not None:
            return self._contracts
        contracts: dict[str, Contract] = {}
        for path in sorted(self.root.rglob("*.yml")) + sorted(self.root.rglob("*.yaml")):
            contract = self._load(path)
            if contract.dataset_id in contracts:
                raise ContractError(
                    f"duplicate dataset_id {contract.dataset_id!r}: "
                    f"{contracts[contract.dataset_id].path} and {path}"
                )
            contracts[contract.dataset_id] = contract
        self._contracts = contracts
        return contracts

    def get(self, dataset_id: str) -> Contract:
        try:
            return self.all()[dataset_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.all())) or "none"
            raise ContractError(
                f"unknown dataset {dataset_id!r}; available datasets: {available}"
            ) from exc

    def _load(self, path: Path) -> Contract:
        raw_bytes = path.read_bytes()
        try:
            data = yaml.safe_load(raw_bytes) or {}
        except yaml.YAMLError as exc:
            raise ContractError(f"invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ContractError(f"contract must be a mapping: {path}")
        dataset_id = data.get("dataset_id")
        if not isinstance(dataset_id, str) or not DATASET_ID.fullmatch(dataset_id):
            raise ContractError(f"invalid dataset_id in {path}: {dataset_id!r}")
        target_data = data.get("target") or {}
        try:
            target = Target(
                catalog=validate_identifier(str(target_data["catalog"]), "catalog"),
                namespace=validate_identifier(str(target_data["namespace"]), "namespace"),
                table=validate_identifier(str(target_data["table"]), "table"),
            )
        except KeyError as exc:
            raise ContractError(f"missing target.{exc.args[0]} in {path}") from exc

        source = data.get("source") or {}
        transform = data.get("transform") or {}
        kind = str(data.get("kind") or ("transform" if transform else "source"))
        if kind not in {"source", "transform"}:
            raise ContractError(f"unsupported kind {kind!r} in {path}")
        if kind == "source" and not (source.get("path") or source.get("archived_path")):
            raise ContractError(f"source contract requires source.path in {path}")
        if kind == "transform" and not transform.get("sql"):
            raise ContractError(f"transform contract requires transform.sql in {path}")

        update_data = data.get("update") or {}
        strategy = str(update_data.get("strategy", "create")).lower()
        if strategy not in {"create", "append", "replace", "merge"}:
            raise ContractError(f"unsupported update strategy {strategy!r} in {path}")
        primary_key = tuple(update_data.get("primary_key") or ())
        for key in primary_key:
            validate_identifier(key, "primary key")
        if strategy == "merge" and not primary_key:
            raise ContractError(f"merge strategy requires update.primary_key in {path}")

        quality = data.get("quality") or data.get("expectations") or {}
        if not isinstance(quality, dict):
            raise ContractError(f"quality must be a mapping in {path}")
        inputs = tuple(data.get("inputs") or ())
        for input_id in inputs:
            if not isinstance(input_id, str) or not DATASET_ID.fullmatch(input_id):
                raise ContractError(f"invalid input dataset ID {input_id!r} in {path}")
        return Contract(
            dataset_id=dataset_id,
            path=path,
            digest=hashlib.sha256(raw_bytes).hexdigest(),
            kind=kind,
            owner=str(data.get("owner", "unassigned")),
            description=str(data.get("description", "")),
            target=target,
            update=UpdatePolicy(strategy, primary_key),
            quality=quality,
            source=source,
            transform=transform,
            inputs=inputs,
        )

