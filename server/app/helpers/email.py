import logging
import os
from pathlib import Path

import resend

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY")

resend.api_key = RESEND_API_KEY

CLIENT_DOMAIN = os.getenv("CLIENT_DOMAIN", "http://localhost:3000")


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


def send_general_invite_email(
    to_email: str,
    from_name: str,
) -> bool:
    """
    Send a general invitation email using Resend.

    Args:
        to_email: Recipient email address
        from_name: Name of the person sending the invite

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        signup_link = "https://openpaper.ai/login"
        subject = f"{from_name} invited you to join Open Paper"
        html_content = (
            load_email_template("general_invite.html")
            .replace("{{from_name}}", from_name)
            .replace("{{signup_link}}", signup_link)
        )

        payload = resend.Emails.SendParams = {  # type: ignore
            "from": f"Open Paper <noreply@updates.openpaper.ai>",
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }

        resend.Emails.send(payload)  # type: ignore
        return True

    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}", exc_info=True)
        return False


def send_project_invite_email(
    to_email: str,
    from_name: str,
    project_title: str,
) -> bool:
    """
    Send a project invitation email using Resend.

    Args:
        to_email: Recipient email address
        from_name: Name of the person sending the invite
        project_title: Title of the project

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        invite_link = f"{CLIENT_DOMAIN}/projects?openInvites=true"
        subject = f"{from_name} invited you to collaborate on '{project_title}'"
        html_content = (
            load_email_template("project_invite.html")
            .replace("{{from_name}}", from_name)
            .replace("{{project_title}}", project_title)
            .replace("{{invite_link}}", invite_link)
        )

        payload = resend.Emails.SendParams = {  # type: ignore
            "from": f"Open Paper <noreply@updates.openpaper.ai>",
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }

        resend.Emails.send(payload)  # type: ignore
        return True

    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}", exc_info=True)
        return False


def send_data_table_complete_email(
    to_email: str,
    table_title: str,
    columns: list[str],
    row_count: int,
    project_name: str,
    project_id: str,
    result_id: str,
) -> bool:
    """
    Send an email notification when a data table extraction job completes.

    Args:
        to_email: Recipient email address
        table_title: Title of the data table
        columns: List of column names extracted
        row_count: Number of rows extracted
        project_name: Name of the project containing the data table
        project_id: ID of the project for constructing the view URL
        result_id: ID of the data table result for deep linking

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        view_url = f"{CLIENT_DOMAIN}/projects/{project_id}/tables/{result_id}"
        subject = f"Data table ready: {table_title}"
        columns_str = ", ".join(columns)

        html_content = (
            load_email_template("data_table_complete.html")
            .replace("{{table_title}}", table_title)
            .replace("{{columns}}", columns_str)
            .replace("{{row_count}}", str(row_count))
            .replace("{{project_name}}", project_name)
            .replace("{{view_url}}", view_url)
        )

        payload = resend.Emails.SendParams = {  # type: ignore
            "from": "Open Paper <noreply@updates.openpaper.ai>",
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }

        resend.Emails.send(payload)  # type: ignore
        logger.info(f"Data table complete email sent to {to_email}")
        return True

    except Exception as e:
        logger.error(
            f"Failed to send data table complete email to {to_email}: {e}",
            exc_info=True,
        )
        return False


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
