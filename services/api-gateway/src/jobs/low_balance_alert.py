"""Low balance alert job.

Runs periodically to alert partners with low wallet balances.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from libs.common import get_logger
from libs.db.models import Partner, PartnerWallet
from libs.db.session import get_session_context

logger = get_logger(__name__)


async def check_low_balances() -> int:
    """Check for partners with low wallet balances and trigger alerts.

    Should be run every 6 hours.

    Checks partner wallets where:
    - balance_micros < low_balance_threshold_micros
    - last_low_balance_alert_at is None or > 24 hours ago

    Returns:
        Number of alerts triggered
    """
    now = datetime.now(timezone.utc)
    alert_count = 0

    async with get_session_context() as session:
        # Get wallets below threshold that haven't been alerted recently
        result = await session.execute(
            select(PartnerWallet, Partner)
            .join(Partner, PartnerWallet.partner_id == Partner.id)
            .where(
                PartnerWallet.low_balance_threshold_micros.isnot(None),
                PartnerWallet.balance_micros < PartnerWallet.low_balance_threshold_micros,
            )
        )
        rows = list(result.all())

        for wallet, partner in rows:
            # Check if we already alerted recently (within 24h)
            if wallet.last_low_balance_alert_at:
                hours_since_alert = (
                    now - wallet.last_low_balance_alert_at
                ).total_seconds() / 3600
                if hours_since_alert < 24:
                    continue

            # Trigger alert
            alert_count += 1

            logger.warning(
                "Partner wallet low balance alert",
                partner_id=str(partner.id),
                partner_name=partner.name,
                contact_email=partner.contact_email,
                balance_micros=wallet.balance_micros,
                threshold_micros=wallet.low_balance_threshold_micros,
                balance_dollars=wallet.balance_micros / 1_000_000,
                threshold_dollars=wallet.low_balance_threshold_micros / 1_000_000,
            )

            # Update last alert time
            wallet.last_low_balance_alert_at = now

            # TODO: Send actual notification
            # This could be email, webhook, Slack, etc.
            # For now, just log it
            await _send_low_balance_notification(partner, wallet)

        await session.commit()

    logger.info(
        "Low balance check completed",
        alerts_triggered=alert_count,
    )

    return alert_count


async def _send_low_balance_notification(partner: Partner, wallet: PartnerWallet) -> None:
    """Send low balance notification to partner.

    This is a placeholder for the actual notification implementation.
    Could be email, webhook, Slack, etc. based on partner settings.

    Args:
        partner: Partner model
        wallet: PartnerWallet model
    """
    # Check partner settings for notification preferences
    settings = partner.settings or {}
    notification_config = settings.get("notifications", {})

    # Email notification
    if partner.contact_email and notification_config.get("email_alerts", True):
        logger.info(
            "Would send low balance email",
            to=partner.contact_email,
            partner_name=partner.name,
            balance_dollars=wallet.balance_micros / 1_000_000,
        )
        # TODO: Implement actual email sending
        # await send_email(
        #     to=partner.contact_email,
        #     subject=f"Low Balance Alert: {partner.name}",
        #     body=f"Your wallet balance is ${wallet.balance_micros / 1_000_000:.2f}",
        # )

    # Webhook notification
    webhook_url = notification_config.get("webhook_url")
    if webhook_url:
        logger.info(
            "Would send low balance webhook",
            url=webhook_url,
            partner_id=str(partner.id),
        )
        # TODO: Implement actual webhook call
        # await send_webhook(
        #     url=webhook_url,
        #     payload={
        #         "event": "low_balance_alert",
        #         "partner_id": str(partner.id),
        #         "balance_micros": wallet.balance_micros,
        #         "threshold_micros": wallet.low_balance_threshold_micros,
        #     },
        # )
