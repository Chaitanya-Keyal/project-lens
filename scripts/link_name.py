#!/usr/bin/env python3
"""Link a display name to a contributor profile.

Used to manually resolve name mappings that automated extraction
can't determine — e.g., linking a Telegram display name to a
GitHub login when there's no overlapping name or PR link evidence.

Usage:
    python scripts/link_name.py --github jdlcdl --name "Jean Do"
    python scripts/link_name.py --github jdlcdl --name "Jean Do" --remove
    python scripts/link_name.py --list                # show all profiles with names
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import engine, async_session
from backend.models import Base, ContributorProfile


async def link(github_login: str, name: str, remove: bool = False):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        result = await session.execute(
            select(ContributorProfile)
            .where(ContributorProfile.github_login == github_login)
        )
        profile = result.scalar_one_or_none()

        if not profile:
            print(f"No profile found for '{github_login}'")
            sys.exit(1)

        current = list(profile.display_names)

        excluded = list(profile.excluded_names or [])

        if remove:
            if name in current:
                current.remove(name)
                profile.display_names = sorted(current)
            if name not in excluded:
                excluded.append(name)
                profile.excluded_names = sorted(excluded)
            await session.commit()
            print(f"Removed '{name}' from {github_login} (added to exclusion list)")
            print(f"  Names: {profile.display_names}")
        else:
            # Remove from exclusion list if it was previously excluded
            if name in excluded:
                excluded.remove(name)
                profile.excluded_names = sorted(excluded)
            if name in current:
                print(f"'{name}' already linked to {github_login}")
            else:
                current.append(name)
                profile.display_names = sorted(set(current))
                await session.commit()
                print(f"Linked '{name}' to {github_login}")
                print(f"  Names: {profile.display_names}")


async def list_profiles():
    async with async_session() as session:
        result = await session.execute(
            select(ContributorProfile)
            .order_by(ContributorProfile.last_active.desc())
        )
        for p in result.scalars():
            names = [n for n in p.display_names if n != p.github_login]
            if names:
                print(f"{p.github_login}: {', '.join(names)}")


async def main():
    if "--list" in sys.argv:
        await list_profiles()
        return

    if "--github" not in sys.argv or "--name" not in sys.argv:
        print("Usage: link_name.py --github <login> --name <display_name> [--remove]")
        sys.exit(1)

    github_login = sys.argv[sys.argv.index("--github") + 1]
    name = sys.argv[sys.argv.index("--name") + 1]
    remove = "--remove" in sys.argv

    await link(github_login, name, remove)


if __name__ == "__main__":
    asyncio.run(main())
