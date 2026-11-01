import asyncio
import time

from engleks_api.application.check_readiness import check_readiness


class OkProbe:
    name = "ok"

    async def check(self) -> None:
        return None


class FailingProbe:
    name = "failing"

    async def check(self) -> None:
        raise RuntimeError("down")


class SlowProbe:
    name = "slow"

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    async def check(self) -> None:
        await asyncio.sleep(self.seconds)


async def test_all_probes_ok():
    assert await check_readiness([OkProbe(), OkProbe()]) is True


async def test_no_probes_is_ready():
    assert await check_readiness([]) is True


async def test_failing_probe_not_ready():
    assert await check_readiness([OkProbe(), FailingProbe()]) is False


async def test_slow_probe_times_out():
    start = time.monotonic()
    assert await check_readiness([SlowProbe(seconds=10)], probe_timeout=0.05) is False
    assert time.monotonic() - start < 1
