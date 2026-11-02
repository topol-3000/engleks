import uuid

from engleks_api.domain.ports import InteractionRepository, TextRewriter
from engleks_api.domain.text_processing import AuthContext, Interaction, ProcessedText


async def process_text(
    auth: AuthContext,
    text: str,
    rewriter: TextRewriter,
    interactions: InteractionRepository,
) -> ProcessedText:
    outcome = await rewriter.rewrite(text)
    interaction_id = uuid.uuid7()
    await interactions.add(Interaction(interaction_id, auth.user_id, text, outcome))
    return ProcessedText(
        interaction_id,
        outcome.source_language,
        outcome.output_text,
        outcome.explanations,
    )
