
import asyncio
import os
from dotenv import load_dotenv

from libs.common.auth import hash_api_key
from libs.db import get_session_context, init_db
from libs.db.models import Tenant, User, ApiKey
from sqlalchemy import select

# Load Auth Broker Env
load_dotenv('services/auth-broker/.env')

API_KEY_RAW = os.getenv("API_KEY")
TENANT_SLUG = "demo-tenant"
USER_EXTERNAL_ID = "demo-user"

async def seed():
    print("Connecting to DB...")
    await init_db()
    
    async with get_session_context() as session:
        # 1. Check or Create Tenant
        result = await session.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        tenant = result.scalar_one_or_none()
        
        if not tenant:
            print(f"Creating tenant: {TENANT_SLUG}")
            tenant = Tenant(
                name="Demo Tenant",
                slug=TENANT_SLUG,
                status="active"
            )
            session.add(tenant)
            await session.commit()
            await session.refresh(tenant)
        else:
            print(f"Tenant exists: {tenant.id}")

        # 2. Check or Create API Key
        key_hash = hash_api_key(API_KEY_RAW)
        
        # Look up API key globally (it must be unique)
        result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        api_key = result.scalar_one_or_none()
        
        target_tenant_id = None

        if api_key:
             print(f"API Key exists: {api_key.id} for tenant {api_key.tenant_id}")
             target_tenant_id = api_key.tenant_id
        else:
             print(f"Creating API Key for tenant {tenant.id}")
             api_key = ApiKey(
                 tenant_id=tenant.id,
                 name="Demo Key",
                 key_prefix=API_KEY_RAW[:8],
                 key_hash=key_hash,
                 scopes=["*"],
                 is_active=True
             )
             session.add(api_key)
             target_tenant_id = tenant.id
        
        # Commit API key if new, to ensure we have the ID (though not strictly needed for logic below)
        await session.flush()

        # 3. Check or Create User for the Target Tenant
        # Use target_tenant_id because the key authenticates THAT tenant
        result = await session.execute(select(User).where(User.tenant_id == target_tenant_id, User.external_id == USER_EXTERNAL_ID))
        user = result.scalar_one_or_none()
        
        if not user:
            print(f"Creating user: {USER_EXTERNAL_ID} for tenant {target_tenant_id}")
            user = User(
                tenant_id=target_tenant_id,
                email="demo@example.com",
                external_id=USER_EXTERNAL_ID,
                name="Demo User",
                role="user",
                is_active=True
            )
            session.add(user)
        else:
            print(f"User exists: {user.id}")

        await session.commit()
        print("Seeding Complete!")

if __name__ == "__main__":
    asyncio.run(seed())
