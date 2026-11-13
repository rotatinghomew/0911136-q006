"""指标的版本化基础定义。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    numerator: str
    denominator: str
    version: int


def rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        return None
    return numerator / denominator
