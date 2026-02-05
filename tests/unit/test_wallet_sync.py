
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Fix path to allow importing from src
sys.path.insert(0, os.path.abspath("services/api-gateway/src"))

from services.wallet import WalletService, WalletError
from libs.db.models import Partner, PartnerWallet, PartnerDeposit, DepositStatus

@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.get = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    
    # flush needs to be async
    session.flush = AsyncMock()
    
    return session

@pytest.fixture
def wallet_service(mock_session):
    """Create a WalletService instance with mocked session context."""
    with patch("services.wallet.get_session_context") as mock_ctx:
        # Mock the context manager behavior accurately
        # When 'async with get_session_context() as session:' is called:
        # 1. __aenter__ is awaited and returns the session
        # 2. __aexit__ is awaited on exit
        mock_ctx.return_value.__aenter__.return_value = mock_session
        service = WalletService()
        yield service

@pytest.mark.asyncio
async def test_deposit_syncs_credit_balance(wallet_service, mock_session):
    """Test that deposits update the partner's credit_balance_micros."""
    partner_id = uuid4()
    
    # Mock partner
    partner = Partner(
        id=partner_id,
        name="Test Partner",
        slug="test-partner",
        credit_balance_micros=1000
    )
    
    # Mock wallet
    wallet = PartnerWallet(
        id=uuid4(),
        partner_id=partner_id,
        balance_micros=0,
        total_deposited_micros=0
    )
    
    # Setup execute return values
    # The deposit method calls:
    # 1. select(PartnerWallet) -> returns wallet
    mock_result_wallet = MagicMock()
    mock_result_wallet.scalar_one_or_none.return_value = wallet
    
    # execute calls return result objects
    mock_session.execute.side_effect = [
        mock_result_wallet
    ]
    
    # Mock partner lookup via get()
    mock_session.get.return_value = partner
    
    # Mock redis to avoid connection errors
    with patch("services.wallet.get_redis_client") as mock_redis:
        mock_redis_client = AsyncMock()
        mock_redis.return_value = mock_redis_client
        
        # Execute deposit with auto_complete=True
        await wallet_service.deposit(
            partner_id=partner_id,
            amount_micros=5000,
            auto_complete=True
        )
    
    # Verify partner credit balance was updated
    # Initial 1000 + Deposit 5000 = 6000
    assert partner.credit_balance_micros == 6000
    
    # Verify wallet was updated
    assert wallet.balance_micros == 5000

@pytest.mark.asyncio
async def test_complete_deposit_syncs_credit_balance(wallet_service, mock_session):
    """Test that completing a pending deposit updates credit_balance_micros."""
    partner_id = uuid4()
    wallet_id = uuid4()
    deposit_id = uuid4()
    
    # Mock partner
    partner = Partner(
        id=partner_id,
        credit_balance_micros=2000
    )
    
    # Mock wallet
    wallet = PartnerWallet(
        id=wallet_id,
        partner_id=partner_id,
        balance_micros=1000,
        total_deposited_micros=1000
    )
    
    # Mock deposit
    deposit = PartnerDeposit(
        id=deposit_id,
        wallet_id=wallet_id,
        amount_micros=5000,
        status=DepositStatus.PENDING
    )
    
    # Setup execute results in sequence:
    # 1. select(PartnerDeposit) -> returns deposit
    # 2. select(PartnerWallet) -> returns wallet
    
    mock_result_deposit = MagicMock()
    mock_result_deposit.scalar_one_or_none.return_value = deposit
    
    mock_result_wallet = MagicMock()
    mock_result_wallet.scalar_one.return_value = wallet
    
    mock_session.execute.side_effect = [
        mock_result_deposit,
        mock_result_wallet
    ]
    
    # Mock get partner
    mock_session.get.return_value = partner
    
    with patch("services.wallet.get_redis_client") as mock_redis:
        mock_redis_client = AsyncMock()
        mock_redis.return_value = mock_redis_client
        
        # Execute complete_deposit
        await wallet_service.complete_deposit(deposit_id)
    
    # Verify partner credit balance was updated
    # Initial 2000 + Deposit 5000 = 7000
    assert partner.credit_balance_micros == 7000
    
    # Verify wallet was updated
    assert wallet.balance_micros == 6000
