"""SSO login flow against a mocked TUM-Live + Shibboleth IdP (no real credentials)."""

from __future__ import annotations

import os
import stat
import sys

import httpx
import pytest

from lecture_studio.tumlive.auth import LoginManager, parse_form
from lecture_studio.tumlive.client import AuthRequired, SessionStore, TumLiveClient

LS_PAGE = """<form name="form1" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
<input type="hidden" name="csrf_token" value="_c1" />
<input name="shib_idp_ls_success.shib_idp_session_ss" type="hidden" value="false" />
<input name="shib_idp_ls_supported" type="hidden" /><input name="_eventId_proceed" type="hidden" />
<input type="submit" value="Continue" /></form>"""

CRED_PAGE = """<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s2" method="post">
<input type="hidden" name="csrf_token" value="_c2" />
<label for="username">Username</label><input id="username" name="j_username" type="text"/>
<label for="password">Password</label><input id="password" name="j_password" type="password"/>
<input type="checkbox" name="donotcache-dummy" value="1" disabled="disabled" />
<input type="hidden" name="donotcache" value="1" />
{error}
<button id="btnLogin" type="submit" name="_eventId_proceed">Login</button>
<button type="submit" name="_eventId_passkey">Passkey</button></form>"""

MFA_PAGE = """<h1>Two-factor authentication</h1>
<form action="/idp/profile/SAML2/Redirect/SSO?execution=e1s3" method="post">
<input type="hidden" name="csrf_token" value="_c3" />
<label for="otp">Code</label><input id="otp" name="j_tokenNumber" type="text"/>
<button type="submit" name="_eventId_proceed">Verify</button></form>"""

SAML_POST = """<form action="https://live.rbg.tum.de/shib" method="post">
<input type="hidden" name="SAMLResponse" value="PHNhbWw+"/><input type="hidden" name="RelayState" value="x"/>
<input type="submit" value="Continue"/></form>"""


class FakeIdP:
    def __init__(self, mfa: bool = False):
        self.mfa = mfa
        self.seen_passwords: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        html = {"content-type": "text/html"}
        if url == "https://live.rbg.tum.de/saml/out":
            return httpx.Response(
                302,
                headers={"location": "https://login.tum.de/idp/profile/SAML2/Redirect/SSO?execution=e1s1"},
            )
        if "execution=e1s1" in url and req.method == "GET":
            return httpx.Response(200, text=LS_PAGE, headers=html)
        if "execution=e1s1" in url:
            assert b"csrf_token=_c1" in req.content
            return httpx.Response(200, text=CRED_PAGE.format(error=""), headers=html)
        if "execution=e1s2" in url:
            form = dict(httpx.QueryParams(req.content.decode()))
            assert (
                form["csrf_token"] == "_c2" and "_eventId_proceed" in form and "_eventId_passkey" not in form
            )
            self.seen_passwords.append(form["j_password"])
            if form["j_username"] != "ge12abc" or form["j_password"] != "secret":
                err = '<section class="output--error">The password you entered was incorrect.</section>'
                return httpx.Response(200, text=CRED_PAGE.format(error=err), headers=html)
            return httpx.Response(200, text=MFA_PAGE if self.mfa else SAML_POST, headers=html)
        if "execution=e1s3" in url:
            form = dict(httpx.QueryParams(req.content.decode()))
            if form.get("j_tokenNumber") != "123456":
                return httpx.Response(200, text=MFA_PAGE, headers=html)
            return httpx.Response(200, text=SAML_POST, headers=html)
        if url == "https://live.rbg.tum.de/shib":
            assert b"SAMLResponse=PHNhbWw%2B" in req.content
            return httpx.Response(
                302,
                headers={
                    "location": "https://live.rbg.tum.de/",
                    "set-cookie": "jwt=SESSIONTOKEN; Path=/; HttpOnly; Secure",
                },
            )
        if url == "https://live.rbg.tum.de/":
            return httpx.Response(200, text="<html>spa</html>", headers=html)
        return httpx.Response(404)


async def test_sso_login_success():
    idp = FakeIdP()
    mgr = LoginManager("https://live.rbg.tum.de", transport=httpx.MockTransport(idp))
    step = await mgr.login("sso", "ge12abc", "secret")
    assert step.status == "ok" and step.jwt == "SESSIONTOKEN"
    assert idp.seen_passwords == ["secret"]  # submitted exactly once
    assert not mgr.flows  # nothing kept in memory after success


async def test_sso_wrong_password_reports_idp_message():
    mgr = LoginManager("https://live.rbg.tum.de", transport=httpx.MockTransport(FakeIdP()))
    step = await mgr.login("sso", "ge12abc", "wrong")
    assert step.status == "error"
    assert "incorrect" in step.message


async def test_sso_mfa_is_interactive_not_bypassed():
    mgr = LoginManager("https://live.rbg.tum.de", transport=httpx.MockTransport(FakeIdP(mfa=True)))
    step = await mgr.login("sso", "ge12abc", "secret")
    assert step.status == "needs_input"
    assert step.inputs == [{"name": "j_tokenNumber", "type": "text", "label": "Code"}]
    assert "Two-factor" in step.message
    bad = await mgr.resume(step.flow_id, {"j_tokenNumber": "000000"}, None)
    assert bad.status == "needs_input"
    ok = await mgr.resume(bad.flow_id, {"j_tokenNumber": "123456"}, None)
    assert ok.status == "ok" and ok.jwt == "SESSIONTOKEN"


async def test_login_refuses_foreign_redirect():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "live.rbg.tum.de":
            return httpx.Response(302, headers={"location": "https://evil.example/phish"})
        return httpx.Response(200, text=CRED_PAGE.format(error=""))

    mgr = LoginManager("https://live.rbg.tum.de", transport=httpx.MockTransport(handler))
    step = await mgr.login("sso", "u", "p")
    assert step.status == "error" and "evil.example" in step.message


async def test_missing_credentials():
    mgr = LoginManager("https://live.rbg.tum.de")
    assert (await mgr.login("sso", "", "")).status == "error"


def test_parse_form_kinds():
    assert parse_form(LS_PAGE, "https://login.tum.de/x").kind == "local_storage"
    f = parse_form(CRED_PAGE.format(error=""), "https://login.tum.de/x")
    assert f.kind == "credentials" and f.submit_name == "_eventId_proceed"
    assert "donotcache-dummy" not in f.fields  # disabled checkbox not submitted
    assert parse_form(SAML_POST, "https://login.tum.de/x").kind == "saml_response"
    assert parse_form(MFA_PAGE, "https://login.tum.de/x").kind == "interactive"


def test_session_store_roundtrip_and_permissions(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    assert store.load() is None
    store.save("TOKEN", "Max")
    data = store.load()
    assert data["jwt"] == "TOKEN" and data["display_name"] == "Max"
    assert "password" not in (tmp_path / "session.json").read_text()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "session.json").st_mode) == 0o600
    store.clear()
    assert store.load() is None


async def test_client_sends_cookie_only_and_maps_401():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["cookie"] = req.headers.get("cookie")
        if req.url.path == "/api/v2/users/me":
            return httpx.Response(200, json={"user": {"id": 7, "name": "Max"}})
        return httpx.Response(401, json={"code": 16})

    client = TumLiveClient(jwt="ABC", transport=httpx.MockTransport(handler))
    assert (await client.me())["name"] == "Max"
    assert seen["cookie"] == "jwt=ABC"
    with pytest.raises(AuthRequired):
        await client.enrolled_courses(
            __import__("lecture_studio.tumlive.models").tumlive.models.Semester(2026, "W")
        )
    await client.aclose()
