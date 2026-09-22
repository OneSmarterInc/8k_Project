import logging
import re

from django.conf import settings
from django.core.mail import (
    EmailMultiAlternatives,
)


logger = logging.getLogger(__name__)


class FilingNotificationService:
    """
    Sends an already-generated SEC filing summary.

    Summary generation and PostgreSQL storage happen elsewhere.
    Email failures never interrupt the watcher flow.
    """

    def __init__(self):
        self.enabled = getattr(
            settings,
            "SEC_EMAIL_NOTIFICATIONS_ENABLED",
            False,
        )

        self.recipient = getattr(
            settings,
            "SEC_ALERT_RECIPIENT_EMAIL",
            "",
        )

    @staticmethod
    def _clean_summary_for_email(
        summary_text,
    ):
        """
        Clean only the email copy of the generated summary.

        PostgreSQL summary content remains unchanged.
        """

        text = str(
            summary_text
            or ""
        ).strip()

        if not text:
            return ""

        # Remove internal citations:
        # [C1], [C25], [C625]
        text = re.sub(
            r"\s*\[C\d+\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # Remove chunk labels:
        # [CHUNK 1], [Chunk 20]
        text = re.sub(
            r"\s*\[\s*CHUNK\s+\d+\s*\]",
            "",
            text,
            flags=re.IGNORECASE,
        )

        # Remove line prefixes:
        # Chunk 1:
        text = re.sub(
            r"(?im)^\s*chunk\s+\d+\s*:\s*",
            "",
            text,
        )

        # Fix spaces before punctuation.
        text = re.sub(
            r"\s+([,.;:!?])",
            r"\1",
            text,
        )

        # Remove trailing spaces.
        text = "\n".join(
            line.rstrip()
            for line in text.splitlines()
        )

        # Avoid excessive blank lines.
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return text.strip()

    @staticmethod
    def _format_item_codes(
        item_codes,
    ):
        values = []
        seen = set()

        for item in item_codes or ():
            value = str(
                item
            ).strip()

            if not value:
                continue

            if value in seen:
                continue

            seen.add(value)
            values.append(value)

        if not values:
            return "N/A"

        return ", ".join(values)

    @classmethod
    def _build_body(
        cls,
        *,
        ticker,
        form_type,
        filename,
        filing_date,
        accession_number,
        local_path,
        sec_url,
        clean_summary,
        accepted_at_display,
        entry_session,
        item_codes,
        item_verification_status,
        company_verification_status,
        manual_audit_status,
    ):
        """
        Build the plain-text SEC filing notification email.
        """

        details = [
            (
                "A new SEC filing has been detected "
                "and processed successfully."
            ),
            "",
            "COMPANY / FILING DETAILS",
            "-" * 60,
            f"Ticker: {ticker}",
            f"Form Type: {form_type}",
            (
                "Filing Date: "
                f"{filing_date or 'N/A'}"
            ),
        ]

        # ---------------------------------------------------------
        # EDGAR acceptance timestamp.
        # ---------------------------------------------------------

        if accepted_at_display:
            details.append(
                "EDGAR Accepted: "
                f"{accepted_at_display}"
            )

        # ---------------------------------------------------------
        # Calculated market entry session.
        # ---------------------------------------------------------

        if entry_session:
            details.append(
                "Entry Session: "
                f"{entry_session}"
            )

        # ---------------------------------------------------------
        # Structured 8-K metadata.
        # ---------------------------------------------------------

        if (
            str(form_type)
            .strip()
            .upper()
            == "8-K"
        ):
            details.append(
                "SEC Item Codes: "
                f"{cls._format_item_codes(item_codes)}"
            )

            details.append(
                "Item Verification: "
                f"{item_verification_status or 'NOT AVAILABLE'}"
            )

        # ---------------------------------------------------------
        # Company identity verification.
        # ---------------------------------------------------------

        if company_verification_status:
            details.append(
                "Company Verification: "
                f"{company_verification_status}"
            )

        # ---------------------------------------------------------
        # Remaining filing details.
        # ---------------------------------------------------------

        details.extend(
            [
                (
                    "Accession Number: "
                    f"{accession_number or 'N/A'}"
                ),
                (
                    "Filename: "
                    f"{filename or 'N/A'}"
                ),
                "",
                "",
                "FILING SUMMARY",
                "-" * 60,
                clean_summary,
                "",
                "",
                "SOURCE INFORMATION",
                "-" * 60,
                "SEC Filing:",
                sec_url or "N/A",
                "",
                "Downloaded File:",
                local_path or "N/A",
                "",
                "",
                (
                    "This notification was generated "
                    "automatically by the SEC Filing Watcher."
                ),
            ]
        )

        return "\n".join(
            details
        ).strip()

    @classmethod
    def _build_html_body(
        cls,
        *,
        ticker,
        form_type,
        filename,
        filing_date,
        accession_number,
        local_path,
        sec_url,
        clean_summary,
        accepted_at_display,
        entry_session,
        item_codes,
        item_verification_status,
        company_verification_status,
        manual_audit_status,
    ):
        """
        Build the HTML version of the SEC filing notification email.
        """
        summary_html = str(clean_summary or "").replace("\n", "<br>")
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #f4f7f6;
                    color: #333333;
                    margin: 0;
                    padding: 20px;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.05);
                    border: 1px solid #e1e4e8;
                }}
                .header {{
                    background-color: #0f172a;
                    color: #ffffff;
                    padding: 24px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                    letter-spacing: -0.5px;
                }}
                .header p {{
                    margin: 8px 0 0 0;
                    color: #94a3b8;
                    font-size: 14px;
                }}
                .content {{
                    padding: 32px 24px;
                }}
                .section-title {{
                    font-size: 12px;
                    text-transform: uppercase;
                    letter-spacing: 1px;
                    color: #64748b;
                    font-weight: 700;
                    margin-bottom: 16px;
                    border-bottom: 1px solid #f1f5f9;
                    padding-bottom: 8px;
                }}
                .details-grid {{
                    display: table;
                    width: 100%;
                    margin-bottom: 32px;
                }}
                .detail-row {{
                    display: table-row;
                }}
                .detail-label {{
                    display: table-cell;
                    padding: 8px 16px 8px 0;
                    color: #64748b;
                    font-size: 14px;
                    font-weight: 500;
                    width: 40%;
                }}
                .detail-value {{
                    display: table-cell;
                    padding: 8px 0;
                    color: #0f172a;
                    font-size: 14px;
                    font-weight: 600;
                }}
                .summary-box {{
                    background-color: #f8fafc;
                    border-left: 4px solid #3b82f6;
                    padding: 16px 20px;
                    margin-bottom: 32px;
                    border-radius: 0 4px 4px 0;
                    font-size: 14px;
                    line-height: 1.6;
                    color: #334155;
                }}
                .btn {{
                    display: inline-block;
                    background-color: #3b82f6;
                    color: #ffffff;
                    text-decoration: none;
                    padding: 10px 20px;
                    border-radius: 4px;
                    font-weight: 500;
                    font-size: 14px;
                    text-align: center;
                }}
                .footer {{
                    background-color: #f8fafc;
                    padding: 24px;
                    text-align: center;
                    border-top: 1px solid #e2e8f0;
                    color: #64748b;
                    font-size: 12px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>New {form_type} Filing</h1>
                    <p>{ticker}</p>
                </div>
                <div class="content">
                    <p style="margin-top: 0; margin-bottom: 24px; color: #475569; font-size: 15px;">
                        A new SEC filing has been detected and processed successfully.
                    </p>
                    
                    <div class="section-title">Company & Filing Details</div>
                    <div class="details-grid">
                        <div class="detail-row">
                            <div class="detail-label">Ticker</div>
                            <div class="detail-value">{ticker}</div>
                        </div>
                        <div class="detail-row">
                            <div class="detail-label">Form Type</div>
                            <div class="detail-value">{form_type}</div>
                        </div>
                        <div class="detail-row">
                            <div class="detail-label">Filing Date</div>
                            <div class="detail-value">{filing_date or 'N/A'}</div>
                        </div>
                        <div class="detail-row">
                            <div class="detail-label">Accession Number</div>
                            <div class="detail-value">{accession_number or 'N/A'}</div>
                        </div>
                        {f'''<div class="detail-row">
                            <div class="detail-label">EDGAR Accepted</div>
                            <div class="detail-value">{accepted_at_display}</div>
                        </div>''' if accepted_at_display else ''}
                        {f'''<div class="detail-row">
                            <div class="detail-label">Entry Session</div>
                            <div class="detail-value">{entry_session}</div>
                        </div>''' if entry_session else ''}
                        {f'''<div class="detail-row">
                            <div class="detail-label">SEC Item Codes</div>
                            <div class="detail-value">{cls._format_item_codes(item_codes)}</div>
                        </div>''' if str(form_type).strip().upper() == "8-K" else ''}
                        {f'''<div class="detail-row">
                            <div class="detail-label">Company Verification</div>
                            <div class="detail-value">{company_verification_status}</div>
                        </div>''' if company_verification_status else ''}
                    </div>

                    <div class="section-title">Filing Summary</div>
                    <div class="summary-box">
                        {summary_html}
                    </div>

                    <div style="text-align: center; margin-top: 32px;">
                        <a href="{sec_url or '#'}" class="btn">View on SEC EDGAR</a>
                    </div>
                </div>
                <div class="footer">
                    This notification was generated automatically by the SEC Filing Watcher.<br>
                    File: {filename or 'N/A'}
                </div>
            </div>
        </body>
        </html>
        """

    def send_new_filing_notification(
        self,
        *,
        ticker,
        form_type,
        filename,
        filing_date=None,
        accession_number=None,
        local_path=None,
        sec_url=None,
        summary_text=None,
        accepted_at_display=None,
        entry_session=None,
        item_codes=None,
        item_verification_status=None,
        company_verification_status=None,
        manual_audit_status=None,
    ):
        """
        Send one filing summary notification.

        All metadata parameters are optional so existing callers
        remain backward compatible.
        """

        # ---------------------------------------------------------
        # Feature switch.
        # ---------------------------------------------------------

        if not self.enabled:
            logger.info(
                "SEC email notification disabled."
            )
            return False

        # ---------------------------------------------------------
        # Recipient configuration.
        # ---------------------------------------------------------

        if not self.recipient:
            logger.warning(
                "SEC email skipped: recipient "
                "is not configured."
            )
            return False

        # ---------------------------------------------------------
        # Clean only the outgoing email copy.
        # ---------------------------------------------------------

        clean_summary = (
            self._clean_summary_for_email(
                summary_text
            )
        )

        if not clean_summary:
            logger.warning(
                "SEC email skipped: empty summary "
                "ticker=%s accession=%s",
                ticker,
                accession_number,
            )
            return False

        # ---------------------------------------------------------
        # Sender configuration.
        # ---------------------------------------------------------

        sender_name = getattr(
            settings,
            "SEC_EMAIL_SENDER_NAME",
            "SEC Filing Watcher",
        )

        sender_email = getattr(
            settings,
            "DEFAULT_FROM_EMAIL",
            "",
        )

        if not sender_email:
            logger.warning(
                "SEC email skipped: "
                "DEFAULT_FROM_EMAIL is empty."
            )
            return False

        sender = (
            f"{sender_name} "
            f"<{sender_email}>"
        )

        # ---------------------------------------------------------
        # Subject.
        # ---------------------------------------------------------

        subject = (
            "[SEC Filing Alert] "
            f"{ticker} - New {form_type} Filing"
        )

        # ---------------------------------------------------------
        # Body.
        # ---------------------------------------------------------

        body = self._build_body(
            ticker=ticker,
            form_type=form_type,
            filename=filename,
            filing_date=filing_date,
            accession_number=(
                accession_number
            ),
            local_path=local_path,
            sec_url=sec_url,
            clean_summary=clean_summary,
            accepted_at_display=(
                accepted_at_display
            ),
            entry_session=(
                entry_session
            ),
            item_codes=(
                item_codes
            ),
            item_verification_status=(
                item_verification_status
            ),
            company_verification_status=(
                company_verification_status
            ),
            manual_audit_status=(
                manual_audit_status
            ),
        )

        # ---------------------------------------------------------
        # Optional reply-to.
        # ---------------------------------------------------------

        reply_to_email = getattr(
            settings,
            "SEC_REPLY_TO_EMAIL",
            "",
        )
        
        html_body = self._build_html_body(
            ticker=ticker,
            form_type=form_type,
            filename=filename,
            filing_date=filing_date,
            accession_number=accession_number,
            local_path=local_path,
            sec_url=sec_url,
            clean_summary=clean_summary,
            accepted_at_display=accepted_at_display,
            entry_session=entry_session,
            item_codes=item_codes,
            item_verification_status=item_verification_status,
            company_verification_status=company_verification_status,
            manual_audit_status=manual_audit_status,
        )

        email = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=sender,
            to=[
                self.recipient,
            ],
            reply_to=(
                [
                    reply_to_email,
                ]
                if reply_to_email
                else None
            ),
        )
        
        email.attach_alternative(html_body, "text/html")

        result = email.send(
            fail_silently=False,
        )

        if result == 1:
            logger.info(
                "SEC filing email sent: "
                "ticker=%s form=%s accession=%s "
                "recipient=%s",
                ticker,
                form_type,
                accession_number,
                self.recipient,
            )

            return True

        logger.warning(
            "SEC email returned unexpected "
            "result=%s ticker=%s accession=%s",
            result,
            ticker,
            accession_number,
        )

        return False