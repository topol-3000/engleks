import uuid

import structlog

from engleks_api.domain.access import AuthContext, BudgetExhausted, RateLimited
from engleks_api.domain.ports import InteractionRepository, TextRewriter, UsageLedger
from engleks_api.domain.text_processing import (
    Interaction,
    ProcessedText,
    RewriteFailed,
)

log = structlog.stdlib.get_logger()


async def process_text(
    auth: AuthContext,
    text: str,
    rewriter: TextRewriter,
    interactions: InteractionRepository,
    ledger: UsageLedger,
) -> ProcessedText:
    admission = await ledger.admit(auth.user_id)
    if admission.requests_this_minute > auth.limits.requests_per_minute:
        log.info(
            "rate_limited",
            user_id=auth.user_id,
            count=admission.requests_this_minute,
            limit=auth.limits.requests_per_minute,
        )
        raise RateLimited
    if admission.tokens_today >= auth.limits.daily_tokens:
        log.info(
            "daily_budget_exhausted",
            user_id=auth.user_id,
            tokens_today=admission.tokens_today,
            limit=auth.limits.daily_tokens,
        )
        raise BudgetExhausted
    try:
        outcome = await rewriter.rewrite(text)
    except RewriteFailed as exc:
        await ledger.record(auth.user_id, exc.usage)
        raise
    await ledger.record(auth.user_id, outcome.usage)
    interaction_id = uuid.uuid7()
    await interactions.add(Interaction(interaction_id, auth.user_id, text, outcome))
    return ProcessedText(
        interaction_id,
        outcome.source_language,
        outcome.output_text,
        outcome.explanations,
    )
