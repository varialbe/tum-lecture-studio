"""Async client for the documented TUM-Live API v2 (gocast `apiv2.proto`).

Authentication is the `jwt` session cookie that TUM-Live sets after a login
(SAML/Shibboleth or the internal form). It is sent only to the TUM-Live host.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .models import PRIORITY_SEARCH_TERMS, Course, Recording, Semester

USER_AGENT = "TUM-Lecture-Studio/0.1 (+local personal study tool)"


class TumLiveError(Exception):
    pass


class AuthRequired(TumLiveError):
    """The request needs a (valid) session."""


class NotFound(TumLiveError):
    pass


class SessionStore:
    """Persists only the TUM-Live session cookie (never the password).

    The file is created with owner-only permissions where the OS supports it.
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not data.get("jwt"):
            return None
        return data

    def save(self, jwt: str, display_name: str | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"jwt": jwt, "display_name": display_name, "saved_at": time.time()})
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class TumLiveClient:
    def __init__(
        self,
        base_url: str = "https://live.rbg.tum.de",
        jwt: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.jwt = jwt
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v2",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def set_jwt(self, jwt: str | None) -> None:
        self.jwt = jwt

    async def _get(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> Any:
        # Explicit header (not a cookie jar) so the session goes to this host only.
        headers = {"Cookie": f"jwt={self.jwt}"} if (auth and self.jwt) else None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._http.get(path, params=params, headers=headers)
            except httpx.TransportError as exc:  # transient network failure
                last_exc = exc
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code in (401, 403) or (
                resp.status_code in (302, 303) and "login" in resp.headers.get("location", "")
            ):
                raise AuthRequired(f"{path}: HTTP {resp.status_code}")
            if resp.status_code == 404:
                raise NotFound(path)
            if resp.status_code >= 500:
                last_exc = TumLiveError(f"{path}: HTTP {resp.status_code}")
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if resp.status_code >= 400:
                raise TumLiveError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise TumLiveError(f"{path}: giving up after retries ({last_exc})")

    # ---- endpoints ---------------------------------------------------------
    async def login_options(self) -> dict[str, Any]:
        return await self._get("/login-options", auth=False)

    async def me(self) -> dict[str, Any] | None:
        """Current user, or None when anonymous / session invalid."""
        if not self.jwt:
            return None
        try:
            data = await self._get("/users/me")
        except (AuthRequired, NotFound):
            return None
        user = data.get("user") or {}
        return user if user.get("id") else None

    async def semesters(self) -> tuple[Semester, list[Semester]]:
        data = await self._get("/semesters", auth=False)
        current = Semester.from_api(data.get("current") or {})
        return current, [Semester.from_api(s) for s in data.get("semesters") or []]

    async def public_courses(self, sem: Semester) -> list[Course]:
        data = await self._get("/courses", {"year": sem.year, "term": sem.term})
        return [Course.from_api(c, "public") for c in data.get("courses") or []]

    async def enrolled_courses(self, sem: Semester) -> list[Course]:
        data = await self._get("/courses/enrolled", {"year": sem.year, "term": sem.term})
        return [Course.from_api(c, "enrolled") for c in data.get("courses") or []]

    async def pinned_courses(self) -> list[Course]:
        data = await self._get("/courses/pinned")
        return [Course.from_api(c, "pinned") for c in data.get("courses") or []]

    async def search_courses(self, query: str, sem: Semester, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._get(
            "/search", {"query": query, "semesters": sem.key, "limit": limit, "coursesOnly": "true"}
        )
        return data.get("courses") or []

    async def course(self, slug: str, sem: Semester) -> Course:
        data = await self._get(f"/courses/{slug}", {"year": sem.year, "term": sem.term})
        return Course.from_api(data.get("course") or {})

    async def stream(self, slug: str, stream_id: int) -> Recording:
        data = await self._get(f"/streams/{slug}/{stream_id}")
        return Recording.from_api(data.get("stream") or data)

    # ---- discovery -----------------------------------------------------------
    async def discover(self, sem: Semester, extra_queries: list[str] | None = None) -> list[Course]:
        """Union of enrolled, pinned, public and search-found courses for a semester.

        Course details (recordings) are fetched lazily by the caller.
        """
        found: dict[str, Course] = {}

        def add(courses: list[Course]) -> None:
            for c in courses:
                if c.semester.key != sem.key or not c.slug:
                    continue
                if c.key in found:
                    found[c.key].source |= c.source
                    found[c.key].pinned |= c.pinned
                else:
                    found[c.key] = c

        tasks: list[Any] = [self.public_courses(sem)]
        if self.jwt:
            tasks += [self.enrolled_courses(sem), self.pinned_courses()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, AuthRequired):
                raise res
            if isinstance(res, list):
                add(res)
        # Search reaches courses visible to signed-in users that aren't enrolled/pinned.
        queries = list(dict.fromkeys((extra_queries or []) + (PRIORITY_SEARCH_TERMS if self.jwt else [])))
        hits = await asyncio.gather(*(self.search_courses(q, sem) for q in queries), return_exceptions=True)
        missing = []
        for res in hits:
            if isinstance(res, list):
                for h in res:
                    key = f"{h.get('year')}{h.get('term')}/{h.get('slug')}"
                    if key not in found and h.get("slug"):
                        missing.append(h["slug"])
        for slug in dict.fromkeys(missing):
            try:
                c = await self.course(slug, sem)
                c.source.add("search")
                add([c])
            except TumLiveError:
                continue
        return sorted(found.values(), key=lambda c: c.name.lower())
