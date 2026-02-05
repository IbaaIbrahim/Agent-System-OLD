"""Partner wallet management endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from ..services.wallet import WalletService, get_wallet_service

logger = get_logger(__name__)

router = APIRouter(tags=["Wallet"])


# ============================================================================
# Dependencies
# ============================================================================


def require_partner_or_owner(request: Request) -> dict:
    """Verify that request is from partner or platform owner.

    Returns:
        Dict with role and partner_id
    """
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    is_partner = getattr(request.state, "is_partner", False)
    partner_id = getattr(request.state, "partner_id", None)

    if is_platform_owner:
        return {"role": "platform_owner", "partner_id": None}

    if is_partner and partner_id:
        return {"role": "partner", "partner_id": partner_id}

    raise HTTPException(
        status_code=403,
        detail="Only partners or platform owners can access wallets",
    )


def require_platform_owner(request: Request) -> None:
    """Verify that request is from platform owner."""
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    if not is_platform_owner:
        raise HTTPException(
            status_code=403,
            detail="Only platform owners can perform this action",
        )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateDepositRequest(BaseModel):
    """Request to create a deposit."""

    amount_micros: int = Field(..., gt=0, description="Amount in microdollars")
    payment_method: str | None = None
    external_transaction_id: str | None = None
    notes: str | None = None
    auto_complete: bool = True


class WalletResponse(BaseModel):
    """Wallet information response."""

    id: str
    partner_id: str
    balance_micros: int
    total_deposited_micros: int
    total_spent_micros: int
    low_balance_threshold_micros: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, wallet) -> "WalletResponse":
        """Create response from database model."""
        return cls(
            id=str(wallet.id),
            partner_id=str(wallet.partner_id),
            balance_micros=wallet.balance_micros,
            total_deposited_micros=wallet.total_deposited_micros,
            total_spent_micros=wallet.total_spent_micros,
            low_balance_threshold_micros=wallet.low_balance_threshold_micros,
            created_at=wallet.created_at,
            updated_at=wallet.updated_at,
        )


class DepositResponse(BaseModel):
    """Deposit information response."""

    id: str
    wallet_id: str
    amount_micros: int
    status: str
    payment_method: str | None
    external_transaction_id: str | None
    notes: str | None
    processed_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, deposit) -> "DepositResponse":
        """Create response from database model."""
        status = (
            deposit.status.value
            if hasattr(deposit.status, "value")
            else deposit.status
        )
        return cls(
            id=str(deposit.id),
            wallet_id=str(deposit.wallet_id),
            amount_micros=deposit.amount_micros,
            status=status,
            payment_method=deposit.payment_method,
            external_transaction_id=deposit.external_transaction_id,
            notes=deposit.notes,
            processed_at=deposit.processed_at,
            created_at=deposit.created_at,
        )


class SetThresholdRequest(BaseModel):
    """Request to set low balance threshold."""

    threshold_micros: int | None = Field(
        None, ge=0, description="Threshold in microdollars (null to disable)"
    )


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/partner/wallet", response_model=WalletResponse)
async def get_wallet(
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """Get partner's wallet information.

    Partners see their own wallet.
    Platform owners must specify partner_id.
    """
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    wallet = await service.get_or_create_wallet(partner_id)
    return WalletResponse.from_model(wallet)


@router.get("/partner/wallet/balance")
async def get_wallet_balance(
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """Get partner's current wallet balance.

    Returns just the balance for quick checks.
    """
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    balance = await service.get_balance(partner_id)
    return {
        "partner_id": str(partner_id),
        "balance_micros": balance,
        "balance_dollars": balance / 1_000_000,
    }


@router.post("/partners/{partner_id}/deposits", response_model=DepositResponse)
async def create_deposit(
    partner_id: UUID,
    body: CreateDepositRequest,
    _=Depends(require_platform_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """Create a deposit to a partner's wallet.

    Only platform owners can create deposits (representing received payments).
    Set auto_complete=false to create a pending deposit that must be
    completed separately.
    """
    deposit = await service.deposit(
        partner_id=partner_id,
        amount_micros=body.amount_micros,
        payment_method=body.payment_method,
        external_transaction_id=body.external_transaction_id,
        notes=body.notes,
        auto_complete=body.auto_complete,
    )

    logger.info(
        "Deposit created",
        partner_id=str(partner_id),
        deposit_id=str(deposit.id),
        amount_micros=body.amount_micros,
        status=deposit.status,
    )

    return DepositResponse.from_model(deposit)


@router.post("/deposits/{deposit_id}/complete", response_model=DepositResponse)
async def complete_deposit(
    deposit_id: UUID,
    _=Depends(require_platform_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """Complete a pending deposit.

    Marks the deposit as completed and credits the partner's wallet.
    Only works for deposits in 'pending' status.
    """
    deposit = await service.complete_deposit(deposit_id)

    logger.info(
        "Deposit completed",
        deposit_id=str(deposit_id),
        amount_micros=deposit.amount_micros,
    )

    return DepositResponse.from_model(deposit)


@router.get("/partner/wallet/deposits", response_model=list[DepositResponse])
async def list_deposits(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """List deposits for a partner's wallet."""
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    # Convert status string to enum if provided
    status_filter = None
    if status:
        from libs.db.models import DepositStatus
        try:
            status_filter = DepositStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Valid values: pending, completed, failed, refunded",
            )

    deposits = await service.list_deposits(
        partner_id=partner_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    return [DepositResponse.from_model(d) for d in deposits]


@router.put("/partner/wallet/threshold", response_model=WalletResponse)
async def set_low_balance_threshold(
    request: Request,
    body: SetThresholdRequest,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: WalletService = Depends(get_wallet_service),
):
    """Set the low balance alert threshold.

    When the wallet balance falls below this threshold, alerts will be
    triggered. Set to null to disable alerts.
    """
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    wallet = await service.set_low_balance_threshold(
        partner_id=partner_id,
        threshold_micros=body.threshold_micros,
    )

    logger.info(
        "Low balance threshold updated",
        partner_id=str(partner_id),
        threshold_micros=body.threshold_micros,
    )

    return WalletResponse.from_model(wallet)
