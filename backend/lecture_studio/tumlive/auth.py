"""TUM-Live login flows.

1. SSO (default): replays what a browser does for TUM-Live's SAML login -
   GET /saml/out -> login.tum.de (Shibboleth IdP) -> credentials form ->
   SAMLResponse auto-POST to /shib -> `jwt` cookie. Any page we don't
   recognise as a plain redirect hop (MFA code, attribute-release consent,
   password-change notice, ...) is surfaced to the user as an interactive
   step; nothing is ever auto-confirmed or bypassed.
2. Internal form: POST /login (TUM-Live's own form, LDAP-backed when enabled).
3. Session import: the user pastes the `jwt` cookie from a browser where they
   are already signed in (works for every IdP feature incl. passkeys).

The password only lives in memory for the duration of a single request.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .client import USER_AGENT

MAX_HOPS = 12
ALLOWED_HOSTS = {"live.rbg.tum.de", "login.tum.de", "tum.live"}


class LoginError(Exception):
    pass


@dataclass
class Form:
    action: str
    method: str
    fields: dict[str, str]
    inputs: list[dict[str, str]]  # visible inputs: name, type, label
    submit_name: str | None
    kind: str  # local_storage | credentials | saml_response | consent | interactive
    message: str = ""


@dataclass
class LoginStep:
    status: str  # ok | needs_input | error
    flow_id: str | None = None
    message: str = ""
    inputs: list[dict[str, str]] = field(default_factory=list)
    choices: list[dict[str, str]] = field(default_factory=list)
    jwt: str | None = None


def _text(el: Tag | None) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def parse_form(html: str, base_url: str) -> Form | None:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not isinstance(form, Tag):
        return None
    action = urljoin(base_url, str(form.get("action") or base_url))
    method = str(form.get("method") or "get").lower()
    fields: dict[str, str] = {}
    visible: list[dict[str, str]] = []
    names: set[str] = set()
    submit_name = None
    for inp in form.find_all(["input", "button", "select", "textarea"]):
        if not isinstance(inp, Tag):
            continue
        name = inp.get("name")
        typ = str(inp.get("type") or ("submit" if inp.name == "button" else "text")).lower()
        if not name:
            continue
        name = str(name)
        names.add(name)
        if typ in ("submit", "button"):
            if submit_name is None and name.startswith("_eventId_proceed"):
                submit_name = name
            continue
        if typ == "checkbox":
            if inp.has_attr("checked") and not inp.has_attr("disabled"):
                fields[name] = str(inp.get("value") or "on")
            continue
        if typ == "radio":
            if inp.has_attr("checked"):
                fields[name] = str(inp.get("value") or "")
            continue
        fields.setdefault(name, str(inp.get("value") or ""))
        if typ != "hidden":
            label = ""
            if inp.get("id"):
                lab = soup.find("label", attrs={"for": str(inp.get("id"))})
                label = _text(lab) if isinstance(lab, Tag) else ""
            visible.append({"name": name, "type": typ, "label": label or name})
    if "_eventId_proceed" in names and submit_name is None:
        submit_name = "_eventId_proceed"

    err = soup.select_one(".output--error, .form-error, .alert-danger, .error, #errorMessage")
    message = _text(err) if isinstance(err, Tag) else ""

    if "SAMLResponse" in fields:
        kind = "saml_response"
    elif "j_password" in fields or any(v["type"] == "password" for v in visible):
        kind = "credentials"
    elif any(n.startswith("shib_idp_ls_") for n in names) and not visible:
        kind = "local_storage"
    elif "_shib_idp_consentOptions" in names or "_shib_idp_consentIds" in names:
        kind = "consent"
        message = (
            message or _text(soup.find("h1") if soup.find("h1") else None) or "Attribute release consent"
        )
    else:
        kind = "interactive"
        heading = soup.find(["h1", "h2"])
        message = (
            message or (_text(heading) if isinstance(heading, Tag) else "") or "Additional step required"
        )
    return Form(action, method, fields, visible, submit_name, kind, message)


class SsoLogin:
    """One in-progress login. Kept in memory between interactive steps."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.id = secrets.token_urlsafe(12)
        self.created = time.time()
        self.pending: Form | None = None
        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            follow_redirects=True,
            timeout=30.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _jwt(self) -> str | None:
        host = urlparse(self.base_url).hostname or ""
        for c in self._http.cookies.jar:
            if c.name == "jwt" and host.endswith(c.domain.lstrip(".")) and c.value:
                return c.value
        return None

    def _check_host(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in ALLOWED_HOSTS and host != urlparse(self.base_url).hostname:
            raise LoginError(f"Unexpected redirect to {host}; aborting login for safety")

    async def _submit(self, form: Form, extra: dict[str, str] | None = None) -> httpx.Response:
        self._check_host(form.action)
        data = dict(form.fields)
        if form.submit_name:
            data[form.submit_name] = ""
        if extra:
            data.update(extra)
        if form.method == "post":
            return await self._http.post(form.action, data=data)
        return await self._http.get(form.action, params=data)

    async def start_sso(self, username: str, password: str) -> LoginStep:
        resp = await self._http.get(f"{self.base_url}/saml/out")
        return await self._drive(resp, {"j_username": username, "j_password": password})

    async def internal_login(self, username: str, password: str) -> LoginStep:
        resp = await self._http.post(
            f"{self.base_url}/login", data={"username": username, "password": password}
        )
        jwt = self._jwt()
        if jwt:
            return LoginStep("ok", jwt=jwt)
        if "error=1" in str(resp.url):
            return LoginStep("error", message="Username or password incorrect (internal TUM-Live login).")
        return LoginStep("error", message=f"Login did not produce a session (ended at {resp.url.path}).")

    async def resume(self, values: dict[str, str], choice: str | None = None) -> LoginStep:
        if not self.pending:
            return LoginStep("error", message="No pending login step.")
        form, self.pending = self.pending, None
        extra = {k: v for k, v in values.items() if k in {i["name"] for i in form.inputs} or k in form.fields}
        if choice:
            form.submit_name = choice
        resp = await self._submit(form, extra)
        return await self._drive(resp, None)

    async def _drive(self, resp: httpx.Response, creds: dict[str, str] | None) -> LoginStep:
        used_creds = False
        for _ in range(MAX_HOPS):
            self._check_host(str(resp.url))
            jwt = self._jwt()
            if jwt:
                return LoginStep("ok", jwt=jwt)
            if resp.status_code >= 400:
                return LoginStep("error", message=f"Login server answered HTTP {resp.status_code}.")
            form = parse_form(resp.text, str(resp.url))
            if form is None:
                return LoginStep(
                    "error",
                    message="Login stopped on a page without a form "
                    f"({urlparse(str(resp.url)).hostname}{resp.url.path}).",
                )
            if form.kind == "local_storage":
                resp = await self._submit(form, {"shib_idp_ls_supported": "false"})
            elif form.kind == "saml_response":
                resp = await self._submit(form)
            elif form.kind == "credentials":
                if creds is None or used_creds:
                    return LoginStep("error", message=form.message or "TUM login rejected the credentials.")
                used_creds = True
                user_field = (
                    "j_username"
                    if "j_username" in form.fields
                    else next((i["name"] for i in form.inputs if i["type"] in ("text", "email")), "username")
                )
                pass_field = (
                    "j_password"
                    if "j_password" in form.fields
                    else next((i["name"] for i in form.inputs if i["type"] == "password"), "password")
                )
                resp = await self._submit(
                    form, {user_field: creds["j_username"], pass_field: creds["j_password"]}
                )
                creds = None  # drop the password reference as early as possible
            else:
                # MFA code, consent, notices: the user decides.
                self.pending = form
                choices = []
                soup = BeautifulSoup(resp.text, "html.parser")
                for b in soup.select("form button[name], form input[type=submit][name]"):
                    nm = str(b.get("name"))
                    if nm.startswith("_eventId"):
                        choices.append({"name": nm, "label": _text(b) or str(b.get("value") or nm)})
                return LoginStep(
                    "needs_input",
                    flow_id=self.id,
                    message=form.message,
                    inputs=[i for i in form.inputs if i["type"] not in ("checkbox", "radio")],
                    choices=choices,
                )
        return LoginStep("error", message="Too many redirects during login.")


class LoginManager:
    """Tracks pending interactive logins (expire after 10 minutes)."""

    TTL = 600

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self.transport = transport
        self.flows: dict[str, SsoLogin] = {}

    async def _gc(self) -> None:
        now = time.time()
        for fid, flow in list(self.flows.items()):
            if now - flow.created > self.TTL:
                await flow.aclose()
                del self.flows[fid]

    async def login(self, method: str, username: str, password: str) -> LoginStep:
        await self._gc()
        if not username or not password:
            return LoginStep("error", message="Username and password are required.")
        flow = SsoLogin(self.base_url, self.transport)
        try:
            if method == "internal":
                step = await flow.internal_login(username, password)
            else:
                step = await flow.start_sso(username, password)
        except (httpx.HTTPError, LoginError) as exc:
            step = LoginStep("error", message=f"Login failed: {exc}")
        return await self._finish(flow, step)

    async def resume(self, flow_id: str, values: dict[str, Any], choice: str | None) -> LoginStep:
        await self._gc()
        flow = self.flows.pop(flow_id, None)
        if not flow:
            return LoginStep("error", message="Login step expired, please start again.")
        try:
            step = await flow.resume({k: str(v) for k, v in values.items()}, choice)
        except (httpx.HTTPError, LoginError) as exc:
            step = LoginStep("error", message=f"Login failed: {exc}")
        return await self._finish(flow, step)

    async def _finish(self, flow: SsoLogin, step: LoginStep) -> LoginStep:
        if step.status == "needs_input":
            self.flows[flow.id] = flow
        else:
            await flow.aclose()
        return step
