"""
features/pc_usage.py
Whole-PC CPU and memory, read the way Windows Task Manager does.

CPU uses the "% Processor Utility" performance counter (Task Manager's CPU
number on Windows 10/11, which accounts for the CPU boosting its speed).
The plain CPU-time figure reads noticeably lower at light load. If the
counter can't be opened, the plain figure is used instead.

Memory "in use" is total minus available, Task Manager's Memory percentage.
"""

from __future__ import annotations

import psutil

_UTILITY_COUNTER = r"\Processor Information(_Total)\% Processor Utility"
_TIME_COUNTER = r"\Processor(_Total)\% Processor Time"


class PcUsageReader:
    def __init__(self):
        self._query = None
        self._counter = None
        self.source = "psutil"
        try:
            import win32pdh

            query = win32pdh.OpenQuery()
            for path in (_UTILITY_COUNTER, _TIME_COUNTER):
                try:
                    add = getattr(win32pdh, "AddEnglishCounter", win32pdh.AddCounter)
                    counter = add(query, path)
                except Exception:
                    continue
                self._query, self._counter = query, counter
                self.source = "utility" if path == _UTILITY_COUNTER else "time"
                break
            if self._query is None:
                win32pdh.CloseQuery(query)
            else:
                win32pdh.CollectQueryData(self._query)  # first sample; rates need two
        except Exception:
            self._query = self._counter = None
            self.source = "psutil"
        if self._query is None:
            try:
                psutil.cpu_percent(interval=None)  # prime, the first call is always 0
            except Exception:
                pass

    def cpu_percent(self) -> float:
        if self._query is not None:
            try:
                import win32pdh

                win32pdh.CollectQueryData(self._query)
                _type, value = win32pdh.GetFormattedCounterValue(self._counter, win32pdh.PDH_FMT_DOUBLE)
                return max(0.0, min(100.0, float(value)))  # Task Manager caps boosted values at 100
            except Exception:
                pass
        try:
            return max(0.0, min(100.0, float(psutil.cpu_percent(interval=None))))
        except Exception:
            return 0.0

    @staticmethod
    def memory_mb() -> tuple[float, float]:
        memory = psutil.virtual_memory()
        total = memory.total / 1024 / 1024
        return (memory.total - memory.available) / 1024 / 1024, total

    def read(self) -> tuple[float, float, float] | None:
        """(used MB, total MB, CPU %), or None if nothing could be read."""
        try:
            used, total = self.memory_mb()
        except Exception:
            return None
        return used, total, self.cpu_percent()

    def close(self) -> None:
        if self._query is not None:
            try:
                import win32pdh

                win32pdh.CloseQuery(self._query)
            except Exception:
                pass
            self._query = None
