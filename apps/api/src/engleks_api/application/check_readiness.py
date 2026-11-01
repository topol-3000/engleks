import asyncio
from collections.abc import Sequence

import structlog

from engleks_api.domain.ports import ReadinessProbe

log = structlog.stdlib.get_logger()


async def check_readiness(probes: Sequence[ReadinessProbe], probe_timeout: float = 2.0) -> bool:
    results = await asyncio.gather(*(_probe_ok(probe, probe_timeout) for probe in probes))
    return all(results)


async def _probe_ok(probe: ReadinessProbe, probe_timeout: float) -> bool:
    try:
        async with asyncio.timeout(probe_timeout):
            await probe.check()
    except Exception as exc:
        log.warning("readiness_probe_failed", probe=probe.name, error=repr(exc))
        return False
    return True
