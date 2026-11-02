import uuid

import structlog

from engleks_api.domain.diff import reconcile
from engleks_api.domain.ports import InteractionRepository, TextRewriter
from engleks_api.domain.text_processing import (
    AuthContext,
    Change,
    Interaction,
    Language,
    ProcessedText,
    RewriteRequest,
)

log = structlog.stdlib.get_logger()


async def process_text(
    auth: AuthContext,
    request: RewriteRequest,
    rewriter: TextRewriter,
    interactions: InteractionRepository,
) -> ProcessedText:
    outcome = await rewriter.rewrite(request)
    if outcome.source_language is Language.EN:
        result = reconcile(request.text, outcome.output_text, outcome.changes)
        log.info(
            "diff_reconciled",
            hunks=result.hunks,
            matched=result.matched,
            unexplained=result.unexplained,
            dropped=result.dropped,
        )
        changes = result.changes
    else:
        # A translation shares no text with the input to align against.
        changes = [
            Change(c.original, c.replacement, c.category, c.explanation, span=None)
            for c in outcome.changes
        ]
    interaction_id = uuid.uuid7()
    await interactions.add(Interaction(interaction_id, auth.user_id, request, outcome, changes))
    return ProcessedText(
        interaction_id, outcome.source_language, request.mode, outcome.output_text, changes
    )
