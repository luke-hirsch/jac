"""Mail through the queue. A slow or down SMTP server must not hold up a signup, and the task can
retry where a request can't. It's a plain email backend, so allauth, the run notifications and
`sendtestemail` all go this way without knowing. The worker sends over QUEUED_EMAIL_BACKEND."""

import logging

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from kombu.exceptions import OperationalError

logger = logging.getLogger(__name__)


def message_to_dict(message) -> dict:
    return {
        "subject": message.subject,
        "body": message.body,
        "from_email": message.from_email,
        "to": list(message.to),
        "cc": list(message.cc),
        "bcc": list(message.bcc),
        "reply_to": list(message.reply_to),
        "headers": dict(message.extra_headers),
        "alternatives": [
            [content, mimetype]
            for content, mimetype in getattr(message, "alternatives", [])
        ],
    }


class QueuedEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        from spa.tasks import send_email

        sent = 0
        for message in email_messages:
            try:
                # argsrepr: verification codes stay out of the worker's log lines.
                send_email.apply_async(
                    args=[message_to_dict(message)], argsrepr="(mail)"
                )
            except OperationalError:
                # A signup still gets its code when the broker is down, the slow way. Over the real
                # backend: message.send() would come straight back in here.
                logger.warning("mail queue unreachable: sending inline")
                get_connection(settings.QUEUED_EMAIL_BACKEND).send_messages([message])
            sent += 1
        return sent
