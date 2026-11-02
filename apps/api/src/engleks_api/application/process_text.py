import uuid

from engleks_api.domain.ports import InteractionRepository, TextRewriter
from engleks_api.domain.text_processing import (
    AuthContext,
    Interaction,
    ProcessedText,
    RewriteRequest,
)


async def process_text(
    auth: AuthContext,
    request: RewriteRequest,
    rewriter: TextRewriter,
    interactions: InteractionRepository,
) -> ProcessedText:
    outcome = await rewriter.rewrite(request)
    interaction_id = uuid.uuid7()
    await interactions.add(Interaction(interaction_id, auth.user_id, request, outcome))
    return ProcessedText(
        interaction_id,
        outcome.source_language,
        request.mode,
        outcome.output_text,
        outcome.explanations,
    )
