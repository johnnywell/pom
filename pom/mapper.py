from __future__ import annotations

from collections import ChainMap, defaultdict
from collections.abc import Iterable
from dataclasses import fields, is_dataclass
from functools import partial
from inspect import Parameter, getmembers, isclass, signature
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    NoReturn,
    Tuple,
    Type,
    TypeVar,
    Union,
)

TS = TypeVar("TS")
TT = TypeVar("TT")

SourceType = Union[Type[TS], Tuple[Type[TS], ...]]
TargetType = Type[TT]
Source = Union[TS, Tuple[TS], SourceType]
MapFunction = Callable[[Any], Any]
MappingDict = Dict[str, Union[str, MapFunction]]
MappingSpec = Union[MappingDict, List[str]]


def prop():
    def prop(x):
        return x

    return prop


class Mapper:
    def __init__(self):
        self.mappings = defaultdict(partial(defaultdict, partial(defaultdict, prop)))
        self.exclusions = defaultdict(partial(defaultdict, list))

    def add_mapping(
        self,
        *,
        source: Union[SourceType, TS],
        target: TargetType,
        mapping: MappingSpec = None,
        exclusions: list = None,
    ):
        self._guard_all_source_has_all_mapping_attrs(source, mapping)
        if isinstance(mapping, List):
            mapping = {name: name for name in mapping}
        source_type = self._get_source_type(source)
        self.mappings[source_type][target].update(mapping or {})
        self.exclusions[source_type][target].extend(exclusions or [])

    def map(
        self,
        source_instance: TS,
        target: Union[TT, type[TT]],
        skip_init: bool = False,
        extra: dict = None,
    ) -> TT:
        """Map source object(s) to target type.

        Args:
            source: Single object or iterable of objects to map from
            target_type: Type to map to
            skip_init: Skip __init__ when creating target instance
            extra: Additional attributes to set on target instance
        """
        extra = extra or {}
        target_is_type = isclass(target)
        target_type: type[TT] = target if target_is_type else type(target)
        skip_init = skip_init or not target_is_type
        source_type = self._get_source_type(source_instance)

        # Get source properties
        source_attrs = self._get_source_attrs(
            source_instance, type(source_instance), target_type
        )

        self._guard_no_missing_attrs(
            source_instance, target_type, source_type, extra, target
        )

        # Get mapping rules
        map = self.mappings[source_type][target_type]

        # Apply mappings
        mapped_attrs = dict(self._map(map, source_attrs))

        return self._build_target(
            skip_init,
            target_is_type,
            target,
            mapped_attrs,
            extra,
            target_type,
            source_instance,
        )

    def _guard_all_source_has_all_mapping_attrs(self, source, mapping: MappingSpec):
        if not mapping:
            return

        mapping_attrs_names = self._get_mapping_attrs_names(mapping)
        source_attrs_names = self._get_attrs_names(source)
        missing_attributes = {
            attr for attr in mapping_attrs_names if attr not in source_attrs_names
        }

        if missing_attributes:
            self._raise_missing_attrs_error(source, missing_attributes)

    def _get_mapping_attrs_names(self, mapping):
        if isinstance(mapping, Mapping):
            return mapping.keys()
        if isinstance(mapping, Iterable):
            return mapping
        raise RuntimeError(
            "Can't get mapping attributes names. Mapping expected to be a Dict or List like object"
        )

    def _get_attrs_names(self, source):
        if isinstance(source, Iterable):
            source_attrs = {m[0] for s in source for m in getmembers(s)} | {
                m[0]
                for s in source
                for m in list(signature(s.__init__).parameters.keys())[1:]
            }
        else:
            source_attrs = {m[0] for m in getmembers(source)} | set(
                list(signature(source.__init__).parameters.keys())[1:]
            )

        return source_attrs

    def _raise_missing_attrs_error(self, source, missing_attributes):
        sorted_missing_attributes = sorted(missing_attributes)
        source_name = self._get_source_class_name(source)
        if isinstance(source_name, str):
            source_name_string = f"source {source_name}"
        elif isinstance(source_name, Iterable):
            source_name_string = (
                f"sources {', '.join(source_name[:-1])} and {source_name[-1]}"
            )
        else:
            raise RuntimeError("Can't define source class name")
        if len(sorted_missing_attributes) <= 1:
            attributes_string = f"attribute {''.join(sorted_missing_attributes)}"
        else:
            attributes_string = f"attributes {', '.join(sorted_missing_attributes[:-1])} and {sorted_missing_attributes[-1]}"
        raise TypeError(
            f"Mapping {attributes_string} not found in {source_name_string}."
        )

    def _get_source_class_name(self, source: Source):
        if isinstance(source, Iterable):
            names = sorted(
                [s.__name__ if isclass(s) else type(s).__name__ for s in source]
            )
            return f"({', '.join(names)})"
        if isclass(source):
            return source.__name__
        if isinstance(source, type) is False:
            return type(source).__name__
        raise RuntimeError("Can't define source class name")

    def _build_target(
        self,
        skip_init,
        target_is_type,
        target,
        mapped_attrs,
        extra,
        target_type,
        source_instance,
    ):
        # Create target instance
        try:
            if skip_init:
                if not target_is_type:
                    return self._set_attrs(target, mapped_attrs, extra)
                else:
                    target_instance = object.__new__(target_type)
                    return self._set_attrs(target_instance, mapped_attrs, extra)
            return self._initialize_target(mapped_attrs, extra, target_type)
        except TypeError as e:
            self._handle_mapping_error(source_instance, target, e)

    def _initialize_target(self, mapped_attrs, extra, target_type):
        return target_type(
            **{
                k: v
                for k, v in mapped_attrs.items()
                if k in set(list(signature(target_type.__init__).parameters.keys())[1:])
            },
            **(extra or {}),
        )

    def _guard_no_missing_attrs(
        self, source_instance, target_type, source_type, extra, target
    ):
        missing_prop = set(self.exclusions[source_type][target_type]) - set(
            extra.keys()
        )

        target_attrs = self._get_target_init_params_names_without_default_values(
            target_type, target
        )

        trouble_props = missing_prop & target_attrs
        if not trouble_props:
            return
        source_name = self._get_source_class_name(source_instance)
        if len(trouble_props) == 1:
            raise RuntimeError(
                f"{target_type.__name__} requires argument {trouble_props.pop()} which is excluded from mapping {source_name} -> {target_type.__name__}."
            )
        if len(trouble_props) > 1:
            sorted_trouble_props_names = sorted(trouble_props)
            trouble_pros_names = f"{', '.join(sorted_trouble_props_names[:-1])} and {sorted_trouble_props_names[-1]}"
            raise RuntimeError(
                f"{target_type.__name__} requires arguments {trouble_pros_names} which are excluded from mapping {source_name} -> {target_type.__name__}."
            )

    def _get_source_type(self, source_instance):
        source_type = (
            tuple(so if isclass(so) else type(so) for so in source_instance)
            if isinstance(source_instance, Iterable)
            else source_instance if isclass(source_instance) else type(source_instance)
        )

        return source_type

    def _get_source_attrs(
        self, source, source_type: type[TT], target_type: type[TS]
    ) -> ChainMap:
        """Extract properties from source object(s)."""
        if isinstance(source, Iterable):
            return ChainMap(
                *[self._select_attrs(so, source_type, target_type) for so in source]
            )
        return ChainMap(self._select_attrs(source, source_type, target_type))

    def _select_attrs(self, source, source_type, target_type):
        return dict(
            self._exclude_attrs(
                self._get_public_attrs(source), source_type, target_type
            )
        )

    def _set_attrs(self, instance: TT, attrs: dict, extra: dict = None) -> TT:
        for name, value in {**attrs, **(extra or {})}.items():
            setattr(instance, name, value)
        return instance

    def _handle_mapping_error(
        self, source, target_type: Type, error: TypeError
    ) -> NoReturn:
        """Handle mapping errors with descriptive messages."""
        if isinstance(source, Iterable):
            source_types = ", ".join(type(so).__name__ for so in source)
            raise TypeError(
                f"Source objects ({source_types}) are missing required properties "
                f"for target object {target_type.__name__}: {error}"
            )
        raise TypeError(
            f"Source object {type(source).__name__} is missing required properties "
            f"for target object {target_type.__name__}: {error}"
        )

    def _get_public_attrs(self, object) -> list[tuple]:
        return [
            s_prop for s_prop in getmembers(object) if not s_prop[0].startswith("_")
        ]

    def _exclude_attrs(self, attrs, source_type, target_type):
        return [
            attr
            for attr in attrs
            if attr[0] not in self.exclusions[source_type][target_type]
        ]

    def _get_target_init_params_names_without_default_values(self, target_type, target):

        t_props_names = self._get_init_params_names(target_type) - {
            t_prop[0] for t_prop in self._get_public_attrs(target)
        }

        return t_props_names

    def _get_init_params_names(self, target_type):
        return set(
            list(
                name
                for name, param in signature(target_type.__init__).parameters.items()
                # get only parameter without default value
                if param.default is Parameter.empty
            )[1:]
        )

    def _map(self, maps: dict[str, Callable], props: Mapping) -> list[tuple]:
        result = []
        for prop_name, prop_value in props.items():
            transform = maps[prop_name]
            if callable(transform):
                mapped_value = transform(prop_value)
                result.append((prop_name, mapped_value))
            if isinstance(transform, str):
                result.append((transform, prop_value))
            if isinstance(transform, tuple):
                result.append((transform[0], transform[1](prop_value)))

        return result
