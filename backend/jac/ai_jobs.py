"""The letter editor's AI tools, as queued jobs: both run on the jac-quick lane, so a long
generation never holds them up, and no web thread hangs on the model server. Nothing is persisted
on the application: the client splices the result into its draft."""

from jac.llm_prompts import ParagraphRewrite, ShortenLetter
from llm_connector.conf import resolve_executor
from llm_connector.jobs import AiJobFailed, ai_job

NOTHING_BACK = "The language model returned nothing — try again."


def _executor(job):
    return resolve_executor(
        job.user, job.payload.get("provider", ""), job.payload.get("model", "")
    )


@ai_job("letter_rewrite")
def letter_rewrite(job) -> dict:
    p = job.payload
    text = ParagraphRewrite(
        p["text"],
        instruction=p.get("instruction", ""),
        language=p.get("language", "en"),
        address_form=p.get("address_form", ""),
        executor=_executor(job),
    ).rewrite()
    if not text:
        raise AiJobFailed(NOTHING_BACK)
    return {"text": text}


@ai_job("letter_shorten")
def letter_shorten(job) -> dict:
    p = job.payload
    body = ShortenLetter(
        body=p["body"],
        executor=_executor(job),
        target_words=p["target_words"],
        language=p.get("language", "en"),
        address_form=p.get("address_form", ""),
    ).shorten()
    if not body:
        raise AiJobFailed(NOTHING_BACK)
    return {"body": body}
