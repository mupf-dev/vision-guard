"""GPU telemetry via NVML (pynvml from nvidia-ml-py)."""
from __future__ import annotations

import logging

log = logging.getLogger("vg.gpu")
_nv = None
_handle = None
_failed = False


def _init():
    global _nv, _handle, _failed
    if _handle is not None or _failed:
        return
    try:
        import pynvml as nv
        nv.nvmlInit()
        _handle = nv.nvmlDeviceGetHandleByIndex(0)
        _nv = nv
    except Exception as e:  # noqa: BLE001
        _failed = True
        log.warning("NVML not available: %s", e)


def gpu_info() -> dict:
    """{name, util (%), mem_used (MiB), mem_total (MiB), temp (°C)} – empty dict without GPU."""
    _init()
    if _handle is None:
        return {}
    nv = _nv
    out: dict = {}
    try:
        name = nv.nvmlDeviceGetName(_handle)
        out["name"] = name.decode() if isinstance(name, bytes) else name
    except Exception:  # noqa: BLE001
        pass
    try:
        out["util"] = int(nv.nvmlDeviceGetUtilizationRates(_handle).gpu)
    except Exception:  # noqa: BLE001
        out["util"] = None
    try:
        m = nv.nvmlDeviceGetMemoryInfo(_handle)
        out["mem_used"] = int(m.used // (1024 * 1024))
        out["mem_total"] = int(m.total // (1024 * 1024))
    except Exception:  # noqa: BLE001
        pass
    try:
        out["temp"] = int(nv.nvmlDeviceGetTemperature(_handle, nv.NVML_TEMPERATURE_GPU))
    except Exception:  # noqa: BLE001
        out["temp"] = None
    return out


def mem_mb() -> tuple[int, int] | None:
    """(frei, gesamt) in MiB laut NVML – ganze GPU, alle Prozesse (unter WSL2 zuverlässiger als
    ``torch.cuda.mem_get_info``, das andere Prozesse nicht sieht). None ohne NVML."""
    _init()
    if _handle is None:
        return None
    try:
        m = _nv.nvmlDeviceGetMemoryInfo(_handle)
        return int(m.free // (1024 * 1024)), int(m.total // (1024 * 1024))
    except Exception:  # noqa: BLE001
        return None


def free_mb() -> int | None:
    m = mem_mb()
    return m[0] if m else None
