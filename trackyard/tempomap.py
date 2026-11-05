"""A chart's tempo map: tempo changes and time signatures, ticks ↔ seconds."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


@dataclass
class TempoMap:
    ppq: int
    tempos: list[tuple[int, float]]  # (tick, bpm), sorted, first at tick 0
    signatures: list[tuple[int, int, int]]  # (tick, numerator, denominator)
    _starts: list[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.tempos or self.tempos[0][0] != 0:
            self.tempos.insert(0, (0, self.tempos[0][1] if self.tempos else 120.0))
        if not self.signatures or self.signatures[0][0] != 0:
            self.signatures.insert(0, (0, 4, 4))
        self._starts = [0.0]
        for (t0, bpm0), (t1, _) in zip(self.tempos, self.tempos[1:]):
            self._starts.append(self._starts[-1] + (t1 - t0) / self.ppq * 60.0 / bpm0)

    def seconds(self, tick: float) -> float:
        i = max(0, bisect.bisect_right([t for t, _ in self.tempos], tick) - 1)
        t0, bpm = self.tempos[i]
        return self._starts[i] + (tick - t0) / self.ppq * 60.0 / bpm

    def tick_at(self, seconds: float) -> float:
        """Inverse of `seconds`: fractional tick at a chart time."""
        i = max(0, bisect.bisect_right(self._starts, seconds) - 1)
        t0, bpm = self.tempos[i]
        return t0 + (seconds - self._starts[i]) * bpm / 60.0 * self.ppq

    def beats(self, end_tick: int) -> tuple[list[int], list[bool]]:
        """Beat ticks up to `end_tick` and whether each is a downbeat.

        One beat = one note of the signature's denominator (quarter in x/4).
        """
        ticks: list[int] = []
        downs: list[bool] = []
        sigs = self.signatures + [(end_tick + 1, 0, 0)]
        for (start, num, den), (nxt, _, _) in zip(sigs, sigs[1:]):
            step = self.ppq * 4 / den
            k = 0
            while start + k * step < min(nxt, end_tick + 1):
                ticks.append(round(start + k * step))
                downs.append(k % num == 0)
                k += 1
        return ticks, downs
