"""Identity resolution and contributor profile management."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from project_lens.models import ContributorProfile as ContributorProfileModel
from project_lens.api.config import LensConfig


def _normalize(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase, strip spaces/dashes/dots."""
    return re.sub(r'[\s\-_.]', '', name.lower())


@dataclass
class ContributorProfile:
    """Public-facing contributor profile."""
    github_login: str
    display_names: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    first_active: datetime | None = None
    last_active: datetime | None = None


class IdentityService:
    """Resolves display names to GitHub logins and manages contributor profiles."""

    def __init__(self, config: LensConfig):
        self._config = config

    async def resolve_display_names(self, names: list[str]) -> dict[str, str]:
        """Map display names to GitHub logins.

        Returns {display_name: github_login} for names that matched.
        Unresolved names are omitted. Handles fuzzy matching
        (case-insensitive, space/dash/dot insensitive) and respects
        excluded_names.
        """
        async with self._config.async_session() as session:
            result = await session.execute(select(ContributorProfileModel))
            profiles = result.scalars().all()

        # Build lookup: normalized name → github_login
        lookup: dict[str, str] = {}
        for p in profiles:
            excluded_normalized = {_normalize(n) for n in (p.excluded_names or [])}
            # The login itself is always a valid match
            lookup[_normalize(p.github_login)] = p.github_login
            for dn in p.display_names:
                norm = _normalize(dn)
                if norm not in excluded_normalized:
                    lookup[norm] = p.github_login

        resolved = {}
        for name in names:
            # Skip email addresses and machine-style IDs
            if "@" in name:
                continue
            login = lookup.get(_normalize(name))
            if login:
                resolved[name] = login
        return resolved

    async def link_display_name(
        self, github_login: str, display_name: str,
    ) -> bool:
        """Link a display name to a contributor profile. Returns False if
        the profile doesn't exist."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(ContributorProfileModel)
                .where(ContributorProfileModel.github_login == github_login)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                return False

            current = list(profile.display_names)
            if display_name in current:
                return True  # already linked

            # Remove from exclusion list if previously excluded
            excluded = list(profile.excluded_names or [])
            if display_name in excluded:
                excluded.remove(display_name)
                profile.excluded_names = sorted(excluded)

            current.append(display_name)
            profile.display_names = sorted(set(current))
            await session.commit()
            return True

    async def unlink_display_name(
        self, github_login: str, display_name: str,
    ) -> bool:
        """Unlink a display name and add it to the exclusion list. Returns
        False if the profile doesn't exist."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(ContributorProfileModel)
                .where(ContributorProfileModel.github_login == github_login)
            )
            profile = result.scalar_one_or_none()
            if not profile:
                return False

            current = list(profile.display_names)
            if display_name in current:
                current.remove(display_name)
                profile.display_names = sorted(current)

            excluded = list(profile.excluded_names or [])
            if display_name not in excluded:
                excluded.append(display_name)
                profile.excluded_names = sorted(excluded)

            await session.commit()
            return True

    async def list_contributor_profiles(self) -> list[ContributorProfile]:
        """Return all contributor profiles ordered by last activity."""
        async with self._config.async_session() as session:
            result = await session.execute(
                select(ContributorProfileModel)
                .order_by(ContributorProfileModel.last_active.desc())
            )
            return [
                ContributorProfile(
                    github_login=p.github_login,
                    display_names=list(p.display_names),
                    stats=dict(p.stats),
                    first_active=p.first_active,
                    last_active=p.last_active,
                )
                for p in result.scalars()
            ]
