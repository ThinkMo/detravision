from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import LogoCleanupQueue, SiteSettings
from backend.storage import FileStorage, ValidatedImage


logger = logging.getLogger(__name__)
DEFAULT_SITE_NAME = "DetraVision"
LOGO_URL = "/api/settings/site/logo"


@dataclass(frozen=True, slots=True)
class BrandSettings:
    site_name: str
    logo_key: str | None

    def public_dict(self) -> dict[str, str | None]:
        return {
            "site_name": self.site_name,
            "logo_url": LOGO_URL if self.logo_key is not None else None,
        }


class BrandService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: FileStorage,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage

    @staticmethod
    def _view(row: SiteSettings | None) -> BrandSettings:
        if row is None:
            return BrandSettings(DEFAULT_SITE_NAME, None)
        return BrandSettings(row.site_name, row.logo_key)

    @staticmethod
    def _locked_singleton(session: Session) -> SiteSettings:
        row = session.scalar(
            select(SiteSettings)
            .where(SiteSettings.id == 1)
            .with_for_update()
        )
        if row is None:
            row = SiteSettings(id=1, site_name=DEFAULT_SITE_NAME)
            session.add(row)
            session.flush()
            return row

        # SQLite ignores FOR UPDATE. A no-op write acquires its database write
        # lock before the Logo pointer is read or changed; MySQL already holds
        # the row lock from the SELECT above.
        session.execute(
            update(SiteSettings)
            .where(SiteSettings.id == row.id)
            .values(site_name=SiteSettings.site_name)
        )
        session.refresh(row)
        return row

    def get(self) -> BrandSettings:
        with self._session_factory() as session:
            return self._view(session.get(SiteSettings, 1))

    def update_site_name(self, site_name: str) -> BrandSettings:
        with self._session_factory.begin() as session:
            row = self._locked_singleton(session)
            row.site_name = site_name
            row.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._view(row)

    def replace_logo(self, image: ValidatedImage) -> BrandSettings:
        with image.open() as stream:
            new_key = self._storage.save(stream, "site-logo", image.suffix)
        old_key: str | None = None
        try:
            with self._session_factory.begin() as session:
                row = self._locked_singleton(session)
                old_key = row.logo_key
                row.logo_key = new_key
                row.updated_at = datetime.now(timezone.utc)
                if old_key is not None and old_key != new_key:
                    pending = session.scalar(
                        select(LogoCleanupQueue).where(
                            LogoCleanupQueue.logo_key == old_key
                        )
                    )
                    if pending is None:
                        session.add(LogoCleanupQueue(logo_key=old_key))
                session.flush()
                result = self._view(row)
        except BaseException:
            try:
                self._storage.delete(new_key)
            except Exception as cleanup_error:
                logger.error(
                    "site_logo_rollback_cleanup_failed error_type=%s",
                    type(cleanup_error).__name__,
                )
            raise

        if old_key is not None and old_key != new_key:
            self.cleanup_pending_logo(old_key)
        return result

    def open_logo(self) -> tuple[BinaryIO, str] | None:
        settings = self.get()
        if settings.logo_key is None:
            return None
        suffix = settings.logo_key.rsplit(".", 1)[-1].casefold()
        media_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "webp": "image/webp",
        }.get(suffix)
        if media_type is None:
            logger.error("stored Logo has unsupported suffix")
            return None
        try:
            return self._storage.open(settings.logo_key), media_type
        except FileNotFoundError:
            logger.error("stored Logo is missing")
            return None

    def cleanup_pending_logo(self, logo_key: str) -> bool:
        """Retry one explicitly queued key while serializing with pointer writes."""
        with self._session_factory.begin() as session:
            current = self._locked_singleton(session)
            pending = session.scalar(
                select(LogoCleanupQueue)
                .where(LogoCleanupQueue.logo_key == logo_key)
                .with_for_update()
            )
            if pending is None or current.logo_key == logo_key:
                return False
            try:
                self._storage.delete(logo_key)
            except OSError as cleanup_error:
                logger.error(
                    "site_logo_cleanup_failed error_type=%s",
                    type(cleanup_error).__name__,
                )
                return False
            session.delete(pending)
            return True

    def cleanup_pending_logos(self) -> int:
        """Retry only keys persisted by committed Logo pointer replacements."""
        with self._session_factory() as session:
            keys = session.scalars(
                select(LogoCleanupQueue.logo_key).order_by(LogoCleanupQueue.id)
            ).all()
        return sum(self.cleanup_pending_logo(key) for key in keys)
