"""Offline payment verification regressions; no provider or production DB calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.models import CreditTransaction, User
from app.routers import billing


@pytest.fixture
def harness(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'billing.sqlite'}",
                           connect_args={"check_same_thread": False})
    User.__table__.create(engine)
    CreditTransaction.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([User(email="buyer@example.com", credits=1),
                    User(email="other@example.com", credits=5)])
        db.add(CreditTransaction(user_email="buyer@example.com", type="purchase",
                                amount=10, amount_paid_inr=499, balance_after=1,
                                payment_id="request123", payment_status="pending"))
        db.commit()
    app = FastAPI()
    app.include_router(billing.router)

    @app.middleware("http")
    async def test_session(request, call_next):
        request.scope["session"] = {"user_email": request.headers.get("x-test-user", "buyer@example.com")}
        return await call_next(request)

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(billing.settings, "instamojo_api_key", "test-key")
    monkeypatch.setattr(billing.settings, "instamojo_auth_token", "test-token")
    monkeypatch.setattr(billing.settings, "instamojo_base_url", "https://www.instamojo.com/api/1.1")
    state = {"success": True, "payment_request": {
        "id": "request123", "payment": {"payment_id": "MOJO123", "status": "Credit",
        "currency": "INR", "amount": "499.00", "buyer_email": "buyer@example.com"}}}
    calls = []
    control = {"body": state, "status": 200, "barrier": None, "timeout": False}

    async def transport(request):
        calls.append(request)
        assert str(request.url) == "https://www.instamojo.com/api/1.1/payment-requests/request123/MOJO123/"
        assert request.headers["X-Api-Key"] == "test-key"
        assert request.headers["X-Auth-Token"] == "test-token"
        if control["barrier"]:
            await asyncio.to_thread(control["barrier"].wait, timeout=10)
        if control["timeout"]:
            raise httpx.ReadTimeout("test timeout", request=request)
        if control.get("raw"):
            return httpx.Response(200, text="not json")
        return httpx.Response(control["status"], json=control["body"])

    original = httpx.AsyncClient
    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(billing.httpx, "AsyncClient", client_factory)
    with TestClient(app) as client:
        yield client, sessions, control, calls
    engine.dispose()


def notify(client, route="webhook", **kwargs):
    fields = {"payment_request_id": "request123", "payment_id": "MOJO123",
              "status": "Credit", "payment_status": "Credit"}
    fields.update(kwargs.pop("fields", {}))
    if route == "webhook":
        return client.post("/billing/webhook", data=fields, **kwargs)
    return client.get("/billing/payment-return", params=fields, follow_redirects=False, **kwargs)


def assert_balance(sessions, credits=1, status="pending"):
    with sessions() as db:
        assert db.query(User).filter_by(email="buyer@example.com").one().credits == credits
        assert db.query(User).filter_by(email="other@example.com").one().credits == 5
        assert db.query(CreditTransaction).one().payment_status == status


@pytest.mark.parametrize("route", ["webhook", "return"])
def test_verified_payment_is_credited_once_across_callbacks(harness, route):
    client, sessions, _, calls = harness
    assert notify(client, route).status_code in (200, 303)
    assert notify(client, "webhook").status_code == 200
    assert notify(client, "return").status_code == 303
    assert_balance(sessions, 11, "completed")
    with sessions() as db:
        assert db.query(CreditTransaction).one().balance_after == 11
    assert len(calls) == 1


@pytest.mark.parametrize("route", ["webhook", "return"])
@pytest.mark.parametrize("field,value", [("status", "Failed"), ("amount", "1.00"),
    ("currency", "USD"), ("buyer_email", "other@example.com"), ("payment_id", "MOJOother"),
    ("amount", "NaN"), ("amount", "bad"), ("amount", None)])
def test_forged_success_and_mismatched_provider_evidence_fail(harness, route, field, value):
    client, sessions, control, _ = harness
    control["body"]["payment_request"]["payment"][field] = value
    assert notify(client, route).status_code == 400
    assert_balance(sessions)


@pytest.mark.parametrize("body", [[], {}, {"success": False},
    {"success": True, "payment_request": {"id": "request123", "payment": []}}])
def test_malformed_provider_evidence_fails_closed(harness, body):
    client, sessions, control, _ = harness
    control["body"] = body
    assert notify(client).status_code == 400
    assert_balance(sessions)


def test_payment_bound_to_original_request(harness):
    client, sessions, control, _ = harness
    control["body"]["payment_request"]["id"] = "another-request"
    assert notify(client).status_code == 400
    assert_balance(sessions)


@pytest.mark.parametrize("fault", ["timeout", "raw", "http"])
def test_provider_failure_can_be_retried(harness, fault):
    client, sessions, control, _ = harness
    if fault == "http": control["status"] = 500
    else: control[fault] = True
    assert notify(client).status_code == 503
    assert_balance(sessions)
    control.update(timeout=False, raw=False, status=200)
    assert notify(client).status_code == 200
    assert_balance(sessions, 11, "completed")


def test_return_cannot_credit_another_account(harness):
    client, sessions, _, calls = harness
    assert notify(client, "return", headers={"x-test-user": "other@example.com"}).status_code == 403
    assert_balance(sessions)
    assert not calls


def test_return_requires_session(harness):
    client, sessions, _, calls = harness
    response = notify(client, "return", headers={"x-test-user": ""})
    assert response.headers["location"] == "/auth/login"
    assert_balance(sessions)
    assert not calls


@pytest.mark.parametrize("request_id", ["", "../payments", "unknown"])
def test_invalid_and_unknown_requests_do_not_call_provider(harness, request_id):
    client, sessions, _, calls = harness
    assert notify(client, fields={"payment_request_id": request_id}).status_code == 400
    assert_balance(sessions)
    assert not calls


def test_missing_credentials_fail_closed(harness, monkeypatch):
    client, sessions, _, calls = harness
    monkeypatch.setattr(billing.settings, "instamojo_auth_token", "")
    assert notify(client).status_code == 503
    assert_balance(sessions)
    assert not calls


def test_missing_account_rolls_back_purchase_claim(harness):
    client, sessions, _, _ = harness
    with sessions() as db:
        db.query(User).filter_by(email="buyer@example.com").delete()
        db.commit()
    assert notify(client).status_code == 409
    with sessions() as db:
        assert db.query(CreditTransaction).one().payment_status == "pending"


def test_simultaneous_webhook_and_return_only_credit_once(harness):
    client, sessions, control, calls = harness
    control["barrier"] = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda route: notify(client, route), ["webhook", "return"]))
    assert sorted(r.status_code for r in results) == [200, 303]
    assert len(calls) == 2
    assert_balance(sessions, 11, "completed")
