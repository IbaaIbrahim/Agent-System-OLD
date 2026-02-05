"""Feature service for managing system features and partner configurations.

Handles feature-to-model routing and cost calculation with partner overrides.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger
from libs.common.exceptions import AgentSystemError
from libs.db.models import (
    ModelPricing,
    PartnerFeatureConfig,
    PartnerPlan,
    SystemFeature,
    TenantSubscription,
)
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Redis cache patterns
FEATURE_CONFIG_CACHE_KEY = "feature:{partner_id}:{feature_slug}"
SYSTEM_FEATURE_CACHE_KEY = "system_feature:{slug}"
CACHE_TTL = 300  # 5 minutes


class FeatureError(AgentSystemError):
    """Feature-specific error."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message=message, status_code=400, details=details)


@dataclass
class FeatureConfig:
    """Resolved feature configuration."""

    feature_id: UUID
    slug: str
    name: str
    provider: str
    model_id: str
    weight_multiplier: float
    is_enabled: bool
    requires_approval: bool


@dataclass
class FeatureCostResult:
    """Result of feature cost calculation."""

    feature_slug: str
    provider: str
    model_id: str
    input_tokens: int
    output_tokens: int
    base_cost_micros: int  # Raw LLM cost
    weighted_cost_micros: int  # After weight multiplier
    tenant_cost_micros: int  # After partner margin
    partner_cost_micros: int  # What partner pays (same as base_cost)
    weight_multiplier: float
    margin_percent: float


class FeatureService:
    """Handles feature-to-model routing and cost calculation."""

    async def get_feature_config(
        self,
        partner_id: UUID,
        feature_slug: str,
    ) -> FeatureConfig:
        """Get effective feature configuration for a partner.

        Merges SystemFeature defaults with PartnerFeatureConfig overrides.

        Args:
            partner_id: Partner identifier
            feature_slug: Feature slug (e.g., "translation", "rag")

        Returns:
            Resolved FeatureConfig

        Raises:
            FeatureError: If feature not found or disabled
        """
        # Try cache first
        redis = await get_redis_client()
        cache_key = FEATURE_CONFIG_CACHE_KEY.format(
            partner_id=partner_id, feature_slug=feature_slug
        )
        cached = await redis.get(cache_key)
        if cached:
            import json
            data = json.loads(cached)
            return FeatureConfig(
                feature_id=UUID(data["feature_id"]),
                slug=data["slug"],
                name=data["name"],
                provider=data["provider"],
                model_id=data["model_id"],
                weight_multiplier=data["weight_multiplier"],
                is_enabled=data["is_enabled"],
                requires_approval=data["requires_approval"],
            )

        async with get_session_context() as session:
            # Get system feature
            feature_result = await session.execute(
                select(SystemFeature).where(
                    SystemFeature.slug == feature_slug,
                    SystemFeature.is_active == True,
                )
            )
            feature = feature_result.scalar_one_or_none()
            if not feature:
                raise FeatureError(
                    f"Feature not found or inactive: {feature_slug}",
                    details={"feature_slug": feature_slug},
                )

            # Get partner override (if any)
            config_result = await session.execute(
                select(PartnerFeatureConfig).where(
                    PartnerFeatureConfig.partner_id == partner_id,
                    PartnerFeatureConfig.feature_id == feature.id,
                )
            )
            partner_config = config_result.scalar_one_or_none()

            # Build resolved config
            is_enabled = True
            provider = feature.default_provider
            model_id = feature.default_model_id
            weight_multiplier = float(feature.weight_multiplier)

            if partner_config:
                is_enabled = partner_config.is_enabled
                if partner_config.provider:
                    provider = partner_config.provider
                if partner_config.model_id:
                    model_id = partner_config.model_id
                if partner_config.weight_multiplier is not None:
                    weight_multiplier = float(partner_config.weight_multiplier)

            if not is_enabled:
                raise FeatureError(
                    f"Feature disabled for partner: {feature_slug}",
                    details={"feature_slug": feature_slug, "partner_id": str(partner_id)},
                )

            config = FeatureConfig(
                feature_id=feature.id,
                slug=feature.slug,
                name=feature.name,
                provider=provider,
                model_id=model_id,
                weight_multiplier=weight_multiplier,
                is_enabled=is_enabled,
                requires_approval=feature.requires_approval,
            )

            # Cache the result
            import json
            cache_data = {
                "feature_id": str(config.feature_id),
                "slug": config.slug,
                "name": config.name,
                "provider": config.provider,
                "model_id": config.model_id,
                "weight_multiplier": config.weight_multiplier,
                "is_enabled": config.is_enabled,
                "requires_approval": config.requires_approval,
            }
            await redis.set(cache_key, json.dumps(cache_data), ex=CACHE_TTL)

            return config

    async def check_feature_enabled(
        self,
        partner_id: UUID,
        plan_id: UUID | None,
        feature_slug: str,
    ) -> bool:
        """Check if feature is enabled for partner and allowed by plan.

        Args:
            partner_id: Partner identifier
            plan_id: Plan identifier (optional)
            feature_slug: Feature slug

        Returns:
            True if feature is enabled and allowed
        """
        try:
            config = await self.get_feature_config(partner_id, feature_slug)
            if not config.is_enabled:
                return False

            # Check plan-level feature restrictions if plan_id provided
            if plan_id:
                async with get_session_context() as session:
                    plan_result = await session.execute(
                        select(PartnerPlan).where(PartnerPlan.id == plan_id)
                    )
                    plan = plan_result.scalar_one_or_none()
                    if plan and plan.features:
                        # Check tools_enabled list if present
                        tools_enabled = plan.features.get("tools_enabled")
                        if tools_enabled is not None and feature_slug not in tools_enabled:
                            return False

            return True

        except FeatureError:
            return False

    async def list_features(
        self,
        partner_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[FeatureConfig]:
        """List all features, optionally with partner overrides.

        Args:
            partner_id: Partner identifier (optional)
            include_inactive: Whether to include inactive features

        Returns:
            List of FeatureConfig
        """
        async with get_session_context() as session:
            # Get all system features
            query = select(SystemFeature).order_by(SystemFeature.slug)
            if not include_inactive:
                query = query.where(SystemFeature.is_active == True)

            result = await session.execute(query)
            features = list(result.scalars().all())

            # Get partner overrides if partner_id provided
            partner_configs: dict[UUID, PartnerFeatureConfig] = {}
            if partner_id:
                config_result = await session.execute(
                    select(PartnerFeatureConfig).where(
                        PartnerFeatureConfig.partner_id == partner_id
                    )
                )
                for config in config_result.scalars():
                    partner_configs[config.feature_id] = config

            # Build resolved configs
            configs = []
            for feature in features:
                partner_config = partner_configs.get(feature.id)

                is_enabled = True
                provider = feature.default_provider
                model_id = feature.default_model_id
                weight_multiplier = float(feature.weight_multiplier)

                if partner_config:
                    is_enabled = partner_config.is_enabled
                    if partner_config.provider:
                        provider = partner_config.provider
                    if partner_config.model_id:
                        model_id = partner_config.model_id
                    if partner_config.weight_multiplier is not None:
                        weight_multiplier = float(partner_config.weight_multiplier)

                configs.append(FeatureConfig(
                    feature_id=feature.id,
                    slug=feature.slug,
                    name=feature.name,
                    provider=provider,
                    model_id=model_id,
                    weight_multiplier=weight_multiplier,
                    is_enabled=is_enabled,
                    requires_approval=feature.requires_approval,
                ))

            return configs

    async def calculate_feature_cost(
        self,
        partner_id: UUID,
        tenant_id: UUID,
        feature_slug: str | None,
        input_tokens: int,
        output_tokens: int,
    ) -> FeatureCostResult:
        """Calculate cost for a feature request.

        Applies feature weight multiplier and partner margin.

        Args:
            partner_id: Partner identifier
            tenant_id: Tenant identifier
            feature_slug: Feature slug (None for generic chat)
            input_tokens: Input token count
            output_tokens: Output token count

        Returns:
            FeatureCostResult with detailed cost breakdown
        """
        # Get feature config
        if feature_slug:
            config = await self.get_feature_config(partner_id, feature_slug)
            provider = config.provider
            model_id = config.model_id
            weight_multiplier = config.weight_multiplier
        else:
            # Default to generic chat
            provider = "openai"
            model_id = "gpt-4o"
            weight_multiplier = 1.0
            feature_slug = "chat"

        # Get model pricing
        async with get_session_context() as session:
            pricing_result = await session.execute(
                select(ModelPricing).where(
                    ModelPricing.provider == provider,
                    ModelPricing.model_id == model_id,
                    ModelPricing.is_active == True,
                )
            )
            pricing = pricing_result.scalar_one_or_none()

            # Default pricing if not found
            if pricing:
                input_price = float(pricing.input_price_per_1k)
                output_price = float(pricing.output_price_per_1k)
            else:
                # Conservative default: $0.01 per 1K tokens
                input_price = 0.01
                output_price = 0.03
                logger.warning(
                    "Model pricing not found, using defaults",
                    provider=provider,
                    model_id=model_id,
                )

            # Get margin from tenant's subscription plan
            margin_percent = 0.0
            sub_result = await session.execute(
                select(TenantSubscription, PartnerPlan)
                .join(PartnerPlan, TenantSubscription.plan_id == PartnerPlan.id)
                .where(TenantSubscription.tenant_id == tenant_id)
            )
            row = sub_result.first()
            if row:
                _, plan = row
                margin_percent = float(plan.margin_percent)

        # Calculate costs in microdollars
        MICRODOLLARS_PER_DOLLAR = 1_000_000

        input_cost = (input_tokens / 1000) * input_price
        output_cost = (output_tokens / 1000) * output_price
        base_cost_dollars = input_cost + output_cost
        base_cost_micros = int(base_cost_dollars * MICRODOLLARS_PER_DOLLAR)

        # Apply weight multiplier
        weighted_cost_micros = int(base_cost_micros * weight_multiplier)

        # Apply partner margin for tenant cost
        tenant_cost_micros = int(weighted_cost_micros * (1 + margin_percent / 100))

        # Partner pays base cost (no margin)
        partner_cost_micros = base_cost_micros

        return FeatureCostResult(
            feature_slug=feature_slug,
            provider=provider,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            base_cost_micros=base_cost_micros,
            weighted_cost_micros=weighted_cost_micros,
            tenant_cost_micros=tenant_cost_micros,
            partner_cost_micros=partner_cost_micros,
            weight_multiplier=weight_multiplier,
            margin_percent=margin_percent,
        )

    # =========================================================================
    # Partner Feature Configuration
    # =========================================================================

    async def configure_feature(
        self,
        partner_id: UUID,
        feature_slug: str,
        provider: str | None = None,
        model_id: str | None = None,
        weight_multiplier: float | None = None,
        is_enabled: bool | None = None,
    ) -> PartnerFeatureConfig:
        """Configure or update a feature for a partner.

        Args:
            partner_id: Partner identifier
            feature_slug: Feature slug
            provider: Override provider
            model_id: Override model
            weight_multiplier: Override weight
            is_enabled: Enable/disable feature

        Returns:
            Updated PartnerFeatureConfig
        """
        async with get_session_context() as session:
            # Get system feature
            feature_result = await session.execute(
                select(SystemFeature).where(SystemFeature.slug == feature_slug)
            )
            feature = feature_result.scalar_one_or_none()
            if not feature:
                raise FeatureError(
                    f"Feature not found: {feature_slug}",
                    details={"feature_slug": feature_slug},
                )

            # Get or create partner config
            config_result = await session.execute(
                select(PartnerFeatureConfig)
                .where(
                    PartnerFeatureConfig.partner_id == partner_id,
                    PartnerFeatureConfig.feature_id == feature.id,
                )
                .with_for_update()
            )
            config = config_result.scalar_one_or_none()

            if not config:
                config = PartnerFeatureConfig(
                    partner_id=partner_id,
                    feature_id=feature.id,
                    is_enabled=True,
                )
                session.add(config)

            # Apply updates
            if provider is not None:
                config.provider = provider if provider else None
            if model_id is not None:
                config.model_id = model_id if model_id else None
            if weight_multiplier is not None:
                config.weight_multiplier = weight_multiplier if weight_multiplier > 0 else None
            if is_enabled is not None:
                config.is_enabled = is_enabled

            await session.commit()
            await session.refresh(config)

            # Invalidate cache
            redis = await get_redis_client()
            cache_key = FEATURE_CONFIG_CACHE_KEY.format(
                partner_id=partner_id, feature_slug=feature_slug
            )
            await redis.client.delete(cache_key)

            logger.info(
                "Partner feature configured",
                partner_id=str(partner_id),
                feature_slug=feature_slug,
                provider=config.provider,
                model_id=config.model_id,
                weight_multiplier=config.weight_multiplier,
                is_enabled=config.is_enabled,
            )

            return config

    # =========================================================================
    # System Feature Management (Platform Admin)
    # =========================================================================

    async def create_system_feature(
        self,
        slug: str,
        name: str,
        default_provider: str,
        default_model_id: str,
        description: str | None = None,
        weight_multiplier: float = 1.0,
        requires_approval: bool = False,
    ) -> SystemFeature:
        """Create a new system feature (platform admin only).

        Args:
            slug: Unique feature identifier
            name: Display name
            default_provider: Default LLM provider
            default_model_id: Default model
            description: Feature description
            weight_multiplier: Cost multiplier
            requires_approval: Whether feature requires admin approval

        Returns:
            Created SystemFeature
        """
        async with get_session_context() as session:
            # Check slug uniqueness
            existing = await session.execute(
                select(SystemFeature).where(SystemFeature.slug == slug)
            )
            if existing.scalar_one_or_none():
                raise FeatureError(
                    f"Feature slug already exists: {slug}",
                    details={"slug": slug},
                )

            feature = SystemFeature(
                slug=slug,
                name=name,
                description=description,
                default_provider=default_provider,
                default_model_id=default_model_id,
                weight_multiplier=weight_multiplier,
                is_active=True,
                requires_approval=requires_approval,
            )
            session.add(feature)
            await session.commit()
            await session.refresh(feature)

            logger.info(
                "System feature created",
                feature_id=str(feature.id),
                slug=slug,
            )

            return feature

    async def update_system_feature(
        self,
        feature_id: UUID,
        **updates,
    ) -> SystemFeature:
        """Update a system feature (platform admin only).

        Args:
            feature_id: Feature identifier
            **updates: Fields to update

        Returns:
            Updated SystemFeature
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(SystemFeature)
                .where(SystemFeature.id == feature_id)
                .with_for_update()
            )
            feature = result.scalar_one_or_none()
            if not feature:
                raise FeatureError(
                    f"Feature not found: {feature_id}",
                    details={"feature_id": str(feature_id)},
                )

            # Apply updates
            allowed_fields = {
                "name", "description", "default_provider", "default_model_id",
                "weight_multiplier", "is_active", "requires_approval",
            }
            for key, value in updates.items():
                if key in allowed_fields:
                    setattr(feature, key, value)

            await session.commit()
            await session.refresh(feature)

            # Invalidate all caches for this feature
            redis = await get_redis_client()
            cache_key = SYSTEM_FEATURE_CACHE_KEY.format(slug=feature.slug)
            await redis.client.delete(cache_key)

            logger.info(
                "System feature updated",
                feature_id=str(feature_id),
                updates=list(updates.keys()),
            )

            return feature


# Singleton instance
_feature_service: FeatureService | None = None


def get_feature_service() -> FeatureService:
    """Get feature service singleton."""
    global _feature_service
    if _feature_service is None:
        _feature_service = FeatureService()
    return _feature_service
