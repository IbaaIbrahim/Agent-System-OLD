"""Partner wallet service for managing partner USD balances.

Partners deposit money into their wallet, which is debited when
their tenants consume LLM resources.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger
from libs.common.exceptions import AgentSystemError
from libs.db.models import (
    DepositStatus,
    Partner,
    PartnerDeposit,
    PartnerWallet,
)
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Redis key patterns
WALLET_BALANCE_KEY = "wallet:partner:{partner_id}:balance"
WALLET_CACHE_TTL = 60  # seconds


class WalletError(AgentSystemError):
    """Wallet-specific error."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message=message, status_code=402, details=details)


class WalletService:
    """Manages partner wallet operations."""

    async def get_or_create_wallet(self, partner_id: UUID) -> PartnerWallet:
        """Get partner wallet, creating if it doesn't exist.

        Args:
            partner_id: Partner identifier

        Returns:
            PartnerWallet instance
        """
        async with get_session_context() as session:
            # Try to get existing wallet
            result = await session.execute(
                select(PartnerWallet).where(PartnerWallet.partner_id == partner_id)
            )
            wallet = result.scalar_one_or_none()

            if wallet:
                return wallet

            # Verify partner exists
            partner_result = await session.execute(
                select(Partner).where(Partner.id == partner_id)
            )
            partner = partner_result.scalar_one_or_none()
            if not partner:
                raise WalletError(
                    f"Partner not found: {partner_id}",
                    details={"partner_id": str(partner_id)},
                )

            # Create new wallet
            wallet = PartnerWallet(
                partner_id=partner_id,
                balance_micros=0,
                total_deposited_micros=0,
                total_spent_micros=0,
            )
            session.add(wallet)
            await session.commit()
            await session.refresh(wallet)

            logger.info(
                "Created partner wallet",
                partner_id=str(partner_id),
                wallet_id=str(wallet.id),
            )

            return wallet

    async def get_balance(self, partner_id: UUID) -> int:
        """Get partner wallet balance.

        Uses Redis cache with DB fallback.

        Args:
            partner_id: Partner identifier

        Returns:
            Balance in microdollars
        """
        redis = await get_redis_client()
        cache_key = WALLET_BALANCE_KEY.format(partner_id=partner_id)

        # Try cache first
        cached = await redis.get(cache_key)
        if cached is not None:
            return int(cached)

        # Cache miss - get from DB
        wallet = await self.get_or_create_wallet(partner_id)
        balance = wallet.balance_micros

        # Cache the result
        await redis.set(cache_key, str(balance), ex=WALLET_CACHE_TTL)

        return balance

    async def deposit(
        self,
        partner_id: UUID,
        amount_micros: int,
        payment_method: str | None = None,
        external_transaction_id: str | None = None,
        notes: str | None = None,
        auto_complete: bool = True,
    ) -> PartnerDeposit:
        """Record a deposit into partner wallet.

        Args:
            partner_id: Partner identifier
            amount_micros: Amount to deposit in microdollars
            payment_method: Payment method (stripe, wire, manual, etc.)
            external_transaction_id: External payment reference
            notes: Optional notes
            auto_complete: If True, immediately mark as completed and credit balance

        Returns:
            PartnerDeposit record
        """
        if amount_micros <= 0:
            raise WalletError(
                "Deposit amount must be positive",
                details={"amount_micros": amount_micros},
            )

        async with get_session_context() as session:
            # Get or create wallet
            result = await session.execute(
                select(PartnerWallet)
                .where(PartnerWallet.partner_id == partner_id)
                .with_for_update()
            )
            wallet = result.scalar_one_or_none()

            if not wallet:
                # Create wallet inline
                wallet = PartnerWallet(
                    partner_id=partner_id,
                    balance_micros=0,
                    total_deposited_micros=0,
                    total_spent_micros=0,
                )
                session.add(wallet)
                await session.flush()

            # Create deposit record
            deposit = PartnerDeposit(
                wallet_id=wallet.id,
                amount_micros=amount_micros,
                status=DepositStatus.PENDING,
                payment_method=payment_method,
                external_transaction_id=external_transaction_id,
                notes=notes,
            )
            session.add(deposit)

            if auto_complete:
                # Mark completed and credit balance
                deposit.status = DepositStatus.COMPLETED
                deposit.processed_at = datetime.now(timezone.utc)
                wallet.balance_micros += amount_micros
                wallet.total_deposited_micros += amount_micros

                # Sync with partner credit pool
                partner = await session.get(Partner, partner_id)
                if partner:
                    current = partner.credit_balance_micros or 0
                    partner.credit_balance_micros = current + amount_micros

            await session.commit()
            await session.refresh(deposit)

            # Invalidate cache
            redis = await get_redis_client()
            cache_key = WALLET_BALANCE_KEY.format(partner_id=partner_id)
            await redis.client.delete(cache_key)

            logger.info(
                "Partner deposit recorded",
                partner_id=str(partner_id),
                deposit_id=str(deposit.id),
                amount_micros=amount_micros,
                status=deposit.status,
            )

            return deposit

    async def complete_deposit(self, deposit_id: UUID) -> PartnerDeposit:
        """Mark a pending deposit as completed and credit the wallet.

        Args:
            deposit_id: Deposit identifier

        Returns:
            Updated PartnerDeposit record
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerDeposit)
                .where(PartnerDeposit.id == deposit_id)
                .with_for_update()
            )
            deposit = result.scalar_one_or_none()

            if not deposit:
                raise WalletError(
                    f"Deposit not found: {deposit_id}",
                    details={"deposit_id": str(deposit_id)},
                )

            if deposit.status != DepositStatus.PENDING:
                raise WalletError(
                    f"Deposit is not pending: {deposit.status}",
                    details={
                        "deposit_id": str(deposit_id),
                        "status": deposit.status,
                    },
                )

            # Get wallet
            wallet_result = await session.execute(
                select(PartnerWallet)
                .where(PartnerWallet.id == deposit.wallet_id)
                .with_for_update()
            )
            wallet = wallet_result.scalar_one()

            # Complete deposit
            deposit.status = DepositStatus.COMPLETED
            deposit.processed_at = datetime.now(timezone.utc)
            wallet.balance_micros += deposit.amount_micros
            wallet.total_deposited_micros += deposit.amount_micros

            # Sync with partner credit pool
            partner = await session.get(Partner, wallet.partner_id)
            if partner:
                current = partner.credit_balance_micros or 0
                partner.credit_balance_micros = current + deposit.amount_micros

            await session.commit()
            await session.refresh(deposit)

            # Invalidate cache
            redis = await get_redis_client()
            cache_key = WALLET_BALANCE_KEY.format(partner_id=wallet.partner_id)
            await redis.client.delete(cache_key)

            logger.info(
                "Deposit completed",
                deposit_id=str(deposit_id),
                amount_micros=deposit.amount_micros,
            )

            return deposit

    async def debit(
        self,
        partner_id: UUID,
        amount_micros: int,
        reason: str,
    ) -> bool:
        """Debit partner wallet for LLM costs.

        Uses atomic Redis decrement for high-throughput scenarios.

        Args:
            partner_id: Partner identifier
            amount_micros: Amount to debit in microdollars
            reason: Reason for debit (e.g., "job:uuid")

        Returns:
            True if successful

        Raises:
            WalletError: If insufficient balance
        """
        if amount_micros <= 0:
            return True  # Nothing to debit

        redis = await get_redis_client()
        cache_key = WALLET_BALANCE_KEY.format(partner_id=partner_id)

        # Ensure cache is populated
        cached = await redis.get(cache_key)
        if cached is None:
            wallet = await self.get_or_create_wallet(partner_id)
            await redis.set(cache_key, str(wallet.balance_micros), ex=WALLET_CACHE_TTL)

        # Atomic decrement
        new_balance = await redis.client.decrby(cache_key, amount_micros)

        if new_balance < 0:
            # Restore and reject
            await redis.client.incrby(cache_key, amount_micros)
            raise WalletError(
                "Insufficient wallet balance",
                details={
                    "partner_id": str(partner_id),
                    "amount_micros": amount_micros,
                    "reason": reason,
                },
            )

        # Update DB asynchronously (eventual consistency)
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerWallet)
                .where(PartnerWallet.partner_id == partner_id)
                .with_for_update()
            )
            wallet = result.scalar_one_or_none()

            if wallet:
                wallet.balance_micros -= amount_micros
                wallet.total_spent_micros += amount_micros
                await session.commit()

        logger.debug(
            "Wallet debited",
            partner_id=str(partner_id),
            amount_micros=amount_micros,
            reason=reason,
            new_balance=new_balance,
        )

        return True

    async def check_low_balance(self, partner_id: UUID) -> bool:
        """Check if wallet is below threshold and should alert.

        Args:
            partner_id: Partner identifier

        Returns:
            True if below threshold and alert should be sent
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerWallet).where(PartnerWallet.partner_id == partner_id)
            )
            wallet = result.scalar_one_or_none()

            if not wallet:
                return False

            # Check if threshold is set
            if wallet.low_balance_threshold_micros is None:
                return False

            # Check if below threshold
            if wallet.balance_micros >= wallet.low_balance_threshold_micros:
                return False

            # Check if we already alerted recently (within 24h)
            if wallet.last_low_balance_alert_at:
                hours_since_alert = (
                    datetime.now(timezone.utc) - wallet.last_low_balance_alert_at
                ).total_seconds() / 3600
                if hours_since_alert < 24:
                    return False

            return True

    async def mark_low_balance_alerted(self, partner_id: UUID) -> None:
        """Mark that a low balance alert was sent.

        Args:
            partner_id: Partner identifier
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerWallet)
                .where(PartnerWallet.partner_id == partner_id)
                .with_for_update()
            )
            wallet = result.scalar_one_or_none()

            if wallet:
                wallet.last_low_balance_alert_at = datetime.now(timezone.utc)
                await session.commit()

    async def set_low_balance_threshold(
        self, partner_id: UUID, threshold_micros: int | None
    ) -> PartnerWallet:
        """Set the low balance alert threshold.

        Args:
            partner_id: Partner identifier
            threshold_micros: Threshold in microdollars (None to disable)

        Returns:
            Updated PartnerWallet
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerWallet)
                .where(PartnerWallet.partner_id == partner_id)
                .with_for_update()
            )
            wallet = result.scalar_one_or_none()

            if not wallet:
                wallet = await self.get_or_create_wallet(partner_id)
                result = await session.execute(
                    select(PartnerWallet)
                    .where(PartnerWallet.partner_id == partner_id)
                    .with_for_update()
                )
                wallet = result.scalar_one()

            wallet.low_balance_threshold_micros = threshold_micros
            await session.commit()
            await session.refresh(wallet)

            logger.info(
                "Low balance threshold updated",
                partner_id=str(partner_id),
                threshold_micros=threshold_micros,
            )

            return wallet

    async def list_deposits(
        self,
        partner_id: UUID,
        status: DepositStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PartnerDeposit]:
        """List deposits for a partner.

        Args:
            partner_id: Partner identifier
            status: Filter by status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of PartnerDeposit records
        """
        async with get_session_context() as session:
            # Get wallet
            wallet_result = await session.execute(
                select(PartnerWallet).where(PartnerWallet.partner_id == partner_id)
            )
            wallet = wallet_result.scalar_one_or_none()

            if not wallet:
                return []

            # Build query
            query = (
                select(PartnerDeposit)
                .where(PartnerDeposit.wallet_id == wallet.id)
                .order_by(PartnerDeposit.created_at.desc())
                .limit(limit)
                .offset(offset)
            )

            if status:
                query = query.where(PartnerDeposit.status == status)

            result = await session.execute(query)
            return list(result.scalars().all())


# Singleton instance
_wallet_service: WalletService | None = None


def get_wallet_service() -> WalletService:
    """Get wallet service singleton."""
    global _wallet_service
    if _wallet_service is None:
        _wallet_service = WalletService()
    return _wallet_service
