"""Console email backend that prints the decoded body — avoids the quoted-printable
soft line breaks (=\\n at column 76) the default console backend produces for long
URLs, which makes password-reset links unusable when copy-pasted from the terminal."""

from django.core.mail.backends.console import EmailBackend as DjangoConsoleBackend


class ReadableConsoleEmailBackend(DjangoConsoleBackend):
    def write_message(self, message):
        self.stream.write(f"Subject: {message.subject}\n")
        self.stream.write(f"From: {message.from_email}\n")
        self.stream.write(f"To: {', '.join(message.to)}\n\n")
        self.stream.write(message.body)
        self.stream.write("\n" + "-" * 79 + "\n")
        self.stream.flush()
