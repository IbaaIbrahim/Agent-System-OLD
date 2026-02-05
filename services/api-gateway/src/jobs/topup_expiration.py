"""Top-up expiration job.

Runs hourly to mark expired top-ups.
"""

from datetime import datetime, timezone

from sqlalchemy import update

from libs.common import get_logger
from libs.db.models import CreditTopUp, TopUpStatus
from libs.db.session import get_session_context

logger = get_logger(__name__)


async def expire_topups() -> int:
    """Mark expired top-ups.

    Should be run hourly (e.g., at :15 past the hour).

    Updates all active top-ups where expires_at <= now to status='expired'.

    Returns:
        Number of top-ups expired
    """
    now = datetime.now(timezone.utc)

    async with get_session_context() as session:
        result = await session.execute(
            update(CreditTopUp)
            .where(
                CreditTopUp.status == TopUpStatus.ACTIVE,
                CreditTopUp.expires_at <= now,
            )
            .values(status=TopUpStatus.EXPIRED)
            .returning(CreditTopUp.id, CreditTopUp.tenant_id, CreditTopUp.remaining_micros)
        )

        expired = list(result.all())
        await session.commit()

    if expired:
        total_expired_credits = sum(row[2] for row in expired)
        logger.info(
            "Top-ups expired",
            count=len(expired),
            total_credits_lost=total_expired_credits,
        )

        # Log individual expirations for audit
        for topup_id, tenant_id, remaining in expired:
            logger.debug(
                "Top-up expired",
                topup_id=str(topup_id),
                tenant_id=str(tenant_id),
                remaining_micros=remaining,
            )

    return len(expired)
