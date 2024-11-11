"""
Base class of memory
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple, Union)

from .const import *


class MemoryUnit:
    def __init__(
        self,
        content: Optional[Dict] = None,
        required_attributes: Optional[Dict] = None,
        activate_timestamp: bool = False,
    ) -> None:
        self._content = {}
        self._activate_timestamp = activate_timestamp
        if required_attributes is not None:
            self._content.update(required_attributes)
        if content is not None:
            self._content.update(content)
        if activate_timestamp and TIME_STAMP_KEY not in self._content:
            self._content[TIME_STAMP_KEY] = time.time()
        for _prop, _value in self._content.items():
            self._set_attribute(_prop, _value)

    def __getitem__(self, key: Any) -> Any:
        return self._content[key]

    def _create_property(self, property_name: str, property_value: Any):

        def _getter(self):
            return getattr(self, f"{SELF_DEFINE_PREFIX}{property_name}", None)

        def _setter(self, value):
            setattr(self, f"{SELF_DEFINE_PREFIX}{property_name}", value)

        setattr(self.__class__, property_name, property(_getter, _setter))
        setattr(self, f"{SELF_DEFINE_PREFIX}{property_name}", property_value)

    def _set_attribute(self, property_name: str, property_value: Any):
        if not hasattr(self, f"{SELF_DEFINE_PREFIX}{property_name}"):
            self._create_property(property_name, property_value)
        else:
            setattr(self, f"{SELF_DEFINE_PREFIX}{property_name}", property_value)

    def update(self, content: Dict) -> None:
        for k, v in content.items():
            if k in self._content:
                orig_v = self._content[k]
                orig_type, new_type = type(orig_v), type(v)
                if not orig_type == new_type:
                    logging.warning(
                        f"Type warning: The type of the value for key '{k}' is changing from `{orig_type.__name__}` to `{new_type.__name__}`!"
                    )
        self._content.update(content)
        for _prop, _value in self._content.items():
            self._set_attribute(_prop, _value)
        if self._activate_timestamp:
            self._set_attribute(TIME_STAMP_KEY, time.time())

    def clear(self) -> None:
        # for _prop, _ in self._content.items():
        #     delattr(self, f"{SELF_DEFINE_PREFIX}{_prop}")
        self._content = {}

    def top_k_values(
        self, key: Any, metric: Callable[[Any], Any], top_k: Optional[int] = None
    ) -> Union[Sequence[Any], Any]:
        values = self._content[key]
        if not isinstance(values, Iterable):
            logging.warning(
                f"the value stored in key `{key}` is not iterable, return value `{values}` instead!"
            )
            return values
        else:
            _sorted_values = sorted(values, key=lambda v: -metric(v))
            if top_k is None:
                return _sorted_values
            if len(_sorted_values) < top_k:
                logging.warning(
                    f"Length of values {len(_sorted_values)} is less than top_k {top_k}, returning all values."
                )
            return _sorted_values[:top_k]


class MemoryBase(ABC):

    def __init__(self) -> None:
        self._memories: Dict[Any, Dict] = {}

    @abstractmethod
    def add(self, msg: Union[Any, Sequence[Any]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def pop(self, index: int) -> Any:
        pass

    @abstractmethod
    def load(
        self, snapshots: Union[Any, Sequence[Any]], reset_memory: bool = False
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def export(
        self,
    ) -> Sequence[Any]:
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    def _fetch_recent_memory(self, recent_n: Optional[int] = None) -> Sequence[Any]:
        _memories = self._memories
        _list_units = list(_memories.keys())
        if recent_n is None:
            return _list_units
        if len(_memories) < recent_n:
            logging.warning(
                f"Length of memory {len(_memories)} is less than recent_n {recent_n}, returning all available memories."
            )
        return _list_units[-recent_n:]

    # interact
    @abstractmethod
    def get(self, key: Any):
        raise NotImplementedError

    @abstractmethod
    def update(self, key: Any, value: Any, store_snapshot: bool):
        raise NotImplementedError

    def __getitem__(self, index: Any) -> Any:
        return list(self._memories.keys())[index]
