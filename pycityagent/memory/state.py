"""
Agent State
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .memory_base import MemoryBase
from .memory_unit import MemoryUnit
from .utils import convert_msg_to_sequence


class StateMemory(MemoryBase):
    """
    A specialized memory management class for storing and retrieving agent state information.

    This class extends `MemoryBase` and provides specific methods for handling agent states,
    such as adding, removing, and loading state data.
    """

    def __init__(
        self,
        msg: Optional[
            Union[MemoryUnit, Sequence[MemoryUnit], Dict, Sequence[Dict]]
        ] = None,
    ) -> None:
        """
        Initializes the StateMemory instance.

        Args:
            msg (Optional[Union[MemoryUnit, Sequence[MemoryUnit], Dict, Sequence[Dict]]], optional):
                Initial data to load into the memory. Can be a single `MemoryUnit`, a sequence of `MemoryUnit` objects,
                a dictionary, or a sequence of dictionaries. Defaults to None.
        """
        super().__init__()
        if msg is not None:
            msg = convert_msg_to_sequence(msg)
            self.load(msg, reset_memory=True)

    def add(self, msg: Union[MemoryUnit, Sequence[MemoryUnit]]) -> None:
        """
        Adds one or more memory units to the memory.

        Args:
            msg (Union[MemoryUnit, Sequence[MemoryUnit]]): A single memory unit or a sequence of memory units to add.
        """
        _memories = self._memories
        msg = convert_msg_to_sequence(msg)
        for unit in msg:
            if unit not in _memories:
                _memories[unit] = {}

    def pop(self, index: int) -> MemoryUnit:
        """
        Removes and returns the memory unit at the specified index.

        Args:
            index (int): The index of the memory unit to remove.

        Returns:
            MemoryUnit: The removed memory unit.

        Raises:
            ValueError: If the index is out of range.
        """
        _memories = self._memories
        try:
            pop_unit = list(_memories.keys())[index]
            _memories.pop(pop_unit)
            return pop_unit
        except IndexError as e:
            raise ValueError(f"Index {index} not in memory!")

    def load(
        self, msg: Union[MemoryUnit, Sequence[MemoryUnit]], reset_memory: bool = False
    ) -> None:
        """
        Loads one or more memory units into the memory.

        Args:
            msg (Union[MemoryUnit, Sequence[MemoryUnit]]): A single memory unit or a sequence of memory units to load.
            reset_memory (bool, optional): If True, clears the existing memory before loading new units. Defaults to False.
        """
        if reset_memory:
            self.reset()
        self.add(msg)

    def reset(self) -> None:
        """
        Resets the memory, clearing all stored memory units.
        """
        self._memories = {}

    @property
    def motion(self) -> Dict:
        """
        Gets the motion state of the most recently added memory unit.

        Returns:
            Dict: The motion state of the most recent memory unit.

        Raises:
            IndexError: If there are no memory units in the memory.
        """
        return self._fetch_recent_memory()[-1].motion  # type: ignore

    @property
    def position(self) -> Tuple[float, float, float]:
        """
        Gets the position state of the most recently added memory unit.

        Returns:
            Tuple[float, float, float]: The position state of the most recent memory unit.

        Raises:
            IndexError: If there are no memory units in the memory.
        """
        return self._fetch_recent_memory()[-1].position  # type: ignore
