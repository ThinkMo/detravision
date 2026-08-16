from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import SiteSettings
from backend.storage import FileStorage, ValidatedImage


logger = logging.getLogger(__name__)
DEFAULT_SITE_NAME = "Safety Detect"
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
    def _singleton(session: Session) -> SiteSettings:
        row = session.get(SiteSettings, 1)
        if row is None:
            row = SiteSettings(id=1, site_name=DEFAULT_SITE_NAME)
            session.add(row)
        return row

    def get(self) -> BrandSettings:
        with self._session_factory() as session:
            return self._view(session.get(SiteSettings, 1))

    def update_site_name(self, site_name: str) -> BrandSettings:
        with self._session_factory.begin() as session:
            row = self._singleton(session)
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
                row = self._singleton(session)
                old_key = row.logo_key
                row.logo_key = new_key
                row.updated_at = datetime.now(timezone.utc)
                session.flush()
                result = self._view(row)
        except BaseException:
            try:
                self._storage.delete(new_key)
            except Exception:
                logger.exception("failed to remove new Logo after settings rollback")
            raise

        if old_key is not None and old_key != new_key:
            try:
                self._storage.delete(old_key)
            except Exception:
                logger.exception("failed to remove superseded Logo; cleanup retry required")
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
