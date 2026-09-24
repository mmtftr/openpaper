import logging
import os
from pathlib import Path

import resend

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY")

resend.api_key = RESEND_API_KEY



def load_email_template(template_name: str) -> str:
    """Load HTML email template from templates directory"""
    # Get the directory of the current file
    current_dir = Path(__file__).parent
    template_path = current_dir / "templates" / template_name

    try:
        with open(template_path, "r", encoding="utf-8") as file:
            return file.read()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Template {template_name} not found at {template_path}"
        )


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str = "",
    from_name: str = "Open Paper",
    from_address: str = "noreply@updates.openpaper.ai",
) -> bool:
    """
    Send a generic email using Resend.

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML content of the email
        text_content: Plain text content (optional)
        from_name: Sender name
        from_address: Sender email address

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        payload = resend.Emails.SendParams = {  # type: ignore
            "from": f"{from_name} <{from_address}>",
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }

        # Add text content if provided
        if text_content:
            payload["text"] = text_content

        resend.Emails.send(payload)  # type: ignore
        logger.info(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}", exc_info=True)
        return False
