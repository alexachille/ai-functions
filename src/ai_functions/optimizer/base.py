"""Abstract base class for optimizers."""

from abc import ABC, abstractmethod
from typing import Any

from ..types.graph import Node, ParameterView
from ..utils._graph import topological_sort


class Optimizer(ABC):
    """Base class for parameter optimizers."""

    @abstractmethod
    def backward(self, node: Node[Any], feedback: str) -> None:
        """Compute gradients for all parameters in the graph leading to ``node``."""

    def consolidate(self, node: Node[Any]) -> None:
        """Consolidate all unique ParameterRefs reachable from the graph."""
        refs = {n.source for n in topological_sort(node) if isinstance(n, ParameterView) and n.requires_grad}
        for ref in refs:
            if ref.gradients:
                ref.consolidate()

    def zero_grad(self, node: Node[Any]) -> None:
        """Consolidate all unique ParameterRefs reachable from the graph."""
        refs = {n.source for n in topological_sort(node) if isinstance(n, ParameterView)}
        for ref in refs:
            ref.gradients.clear()
