"""Strategy registry for the LifeMiles lab.

Each strategy subclasses ``Strategy`` and implements ``async run() ->
StrategyResult``. Importing this package eagerly imports every strategy module
so they self-register in ``ALL_STRATEGIES`` (ordered).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import StrategyResult, get_logger, score_result  # noqa: E402


class Strategy(ABC):
    #: short machine name, used for --only filtering and artifact/log names
    name: str = "base"
    #: human-readable one-liner shown in the leaderboard
    label: str = ""
    #: low | medium | high — dependency & runtime weight, feeds scoring
    cost: str = "low"

    def __init__(self, ctx: dict) -> None:
        self.ctx = ctx
        self.logger = get_logger(self.name)

    @abstractmethod
    async def run(self) -> StrategyResult:
        ...

    async def execute(self) -> StrategyResult:
        """Wrap run() with timing, uniform error handling, and scoring."""
        t0 = time.monotonic()
        self.logger.info(f"START strategy={self.name} :: {self.label}")
        try:
            result = await self.run()
        except Exception as exc:  # noqa: BLE001 - never let one strategy kill the lab
            duration = int((time.monotonic() - t0) * 1000)
            self.logger.error(f"CRASHED {type(exc).__name__}: {exc}")
            result = StrategyResult(
                name=self.name, label=self.label, ok=False,
                duration_ms=duration, cost=self.cost, error=f"{type(exc).__name__}: {exc}",
            )
        if not result.duration_ms:
            result.duration_ms = int((time.monotonic() - t0) * 1000)
        result.score = score_result(result)
        priced = sum(1 for r in result.rows if r.has_any_price())
        self.logger.info(
            f"DONE ok={result.ok} rows={len(result.rows)} priced={priced} "
            f"conf={result.confidence:.2f} {result.duration_ms}ms score={result.score}"
        )
        return result


# Import strategies so they register. Order here is the run/report order.
from . import s4_aggregator_html   # noqa: E402,F401
from . import s1_dom_table         # noqa: E402,F401
from . import s2_network_intercept # noqa: E402,F401
from . import s3_vision            # noqa: E402,F401
from . import s5_api_probe         # noqa: E402,F401

ALL_STRATEGIES: list[type[Strategy]] = [
    s4_aggregator_html.AggregatorHtmlStrategy,
    s1_dom_table.DomTableStrategy,
    s2_network_intercept.NetworkInterceptStrategy,
    s3_vision.VisionStrategy,
    s5_api_probe.ApiProbeStrategy,
]
