from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.api.admin_routes as admin_routes_mod
from app.core.config import get_settings
from app.core.security import ensure_utc
from app.db.database import (
    SessionLocal,
    get_store,
    init_db,
    reset_db_state,
    set_firestore_client_override,
)
from app.db.models import AdminAuditLogORM, LicensePlanORM, UserORM
from app.main import app
from app.schemas.presentation import PresentationDeck, Slide
from app.services.auth_service import create_user_account
from app.services.blob_storage import (
    LocalBlobStorage,
    build_object_key,
    reset_blob_storage_cache,
    set_blob_storage_override,
)
from app.services.licensing_service import (
    apply_license_plan_to_user,
    get_student_daily_38_usage,
    record_usage_and_debit,
)
from app.services.storage import project_storage
from tests._auth_helper import login_as_admin


# =========================================================================
# IN-MEMORY FAKE FIRESTORE CLIENT (FOR OFFLINE END-TO-END VERIFICATION)
# =========================================================================


def _norm_cmp(val: Any) -> Any:
    if isinstance(val, datetime):
        return ensure_utc(val)
    return val


class FakeDocumentSnapshot:
    def __init__(self, doc_id: str, data: Optional[Dict[str, Any]], reference: "FakeDocumentReference") -> None:
        self.id = doc_id
        self._data = deepcopy(data) if data is not None else None
        self.reference = reference

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[Dict[str, Any]]:
        return deepcopy(self._data) if self._data is not None else None


class FakeDocumentReference:
    def __init__(self, client: "FakeFirestoreClient", collection_name: str, doc_id: str) -> None:
        self._client = client
        self._col = collection_name
        self.id = doc_id

    @property
    def reference(self) -> "FakeDocumentReference":
        return self

    def get(self, transaction: Any = None) -> FakeDocumentSnapshot:
        _ = transaction
        data = self._client._collections.get(self._col, {}).get(self.id)
        return FakeDocumentSnapshot(self.id, data, self)

    def set(self, data: Dict[str, Any], merge: bool = False) -> None:
        bucket = self._client._collections.setdefault(self._col, {})
        if merge and self.id in bucket:
            merged = deepcopy(bucket[self.id])
            merged.update(deepcopy(data))
            bucket[self.id] = merged
        else:
            bucket[self.id] = deepcopy(data)

    def update(self, fields: Dict[str, Any]) -> None:
        bucket = self._client._collections.setdefault(self._col, {})
        if self.id not in bucket:
            raise KeyError(f"Missing document {self._col}/{self.id}")
        bucket[self.id].update(deepcopy(fields))

    def delete(self) -> None:
        self._client._collections.setdefault(self._col, {}).pop(self.id, None)


class FakeQuery:
    def __init__(
        self,
        client: "FakeFirestoreClient",
        collection_name: str,
        filters: Optional[List[Tuple[str, str, Any]]] = None,
        orders: Optional[List[Tuple[str, str]]] = None,
        limit_n: Optional[int] = None,
    ) -> None:
        self._client = client
        self._col = collection_name
        self._filters = list(filters or [])
        self._orders = list(orders or [])
        self._limit_n = limit_n

    def where(self, *args: Any, **kwargs: Any) -> "FakeQuery":
        filt = kwargs.get("filter")
        if filt is not None:
            new_filter = (filt.field_path, filt.op_string, filt.value)
        elif len(args) == 3:
            new_filter = (str(args[0]), str(args[1]), args[2])
        else:
            raise ValueError(f"Unsupported where signature: {args} {kwargs}")
        return FakeQuery(
            self._client,
            self._col,
            self._filters + [new_filter],
            self._orders,
            self._limit_n,
        )

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        return FakeQuery(
            self._client,
            self._col,
            self._filters,
            self._orders + [(field, str(direction).upper())],
            self._limit_n,
        )

    def limit(self, n: int) -> "FakeQuery":
        return FakeQuery(self._client, self._col, self._filters, self._orders, int(n))

    def _matches(self, doc: Dict[str, Any]) -> bool:
        for field, op, target in self._filters:
            val = _norm_cmp(doc.get(field))
            cmp_target = _norm_cmp(target)
            if op == "==":
                if val != cmp_target:
                    return False
            elif op == "!=":
                if val == cmp_target:
                    return False
            elif op == "in":
                if val not in list(target):
                    return False
            elif op in (">", ">=", "<", "<="):
                if val is None or cmp_target is None:
                    return False
                if op == ">" and not (val > cmp_target):
                    return False
                if op == ">=" and not (val >= cmp_target):
                    return False
                if op == "<" and not (val < cmp_target):
                    return False
                if op == "<=" and not (val <= cmp_target):
                    return False
            else:
                raise ValueError(f"Unsupported operator: {op}")
        return True

    def stream(self):
        bucket = self._client._collections.get(self._col, {})
        items = [
            (doc_id, deepcopy(data))
            for doc_id, data in bucket.items()
            if self._matches(data)
        ]
        # Stable multi-key sort in reverse order of keys
        for field, direction in reversed(self._orders):
            reverse = direction == "DESCENDING"
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

            def _sort_key(item: Tuple[str, Dict[str, Any]]):
                v = item[1].get(field)
                if isinstance(v, datetime):
                    return (1, ensure_utc(v) or epoch)
                if v is None:
                    return (0, "")
                return (1, v)

            items.sort(key=_sort_key, reverse=reverse)

        if self._limit_n is not None:
            items = items[: self._limit_n]

        return [
            FakeDocumentSnapshot(
                doc_id,
                data,
                FakeDocumentReference(self._client, self._col, doc_id),
            )
            for doc_id, data in items
        ]


class FakeCollectionReference(FakeQuery):
    def document(self, doc_id: Optional[str] = None) -> FakeDocumentReference:
        did = doc_id or uuid4().hex
        return FakeDocumentReference(self._client, self._col, did)


class FakeWriteBatch:
    def __init__(self) -> None:
        self._ops: List[Tuple[str, FakeDocumentReference, Any]] = []

    def set(self, ref: FakeDocumentReference, data: Dict[str, Any], merge: bool = False) -> None:
        self._ops.append(("set", ref, (data, merge)))

    def update(self, ref: FakeDocumentReference, fields: Dict[str, Any]) -> None:
        self._ops.append(("update", ref, fields))

    def delete(self, ref: FakeDocumentReference) -> None:
        self._ops.append(("delete", ref, None))

    def commit(self) -> None:
        for op, ref, payload in self._ops:
            if op == "set":
                data, merge = payload
                ref.set(data, merge=merge)
            elif op == "update":
                ref.update(payload)
            elif op == "delete":
                ref.delete()
        self._ops.clear()


class FakeTransaction:
    def __init__(self) -> None:
        self._max_attempts = 5
        self._read_only = False
        self._id = b"fake-tx"
        self._ops: List[Tuple[str, FakeDocumentReference, Any]] = []

    def _clean_up(self) -> None:
        self._ops.clear()

    def _begin(self, retry_id: Any = None) -> None:
        _ = retry_id

    def _commit(self) -> None:
        for op, ref, payload in self._ops:
            if op == "set":
                data, merge = payload
                ref.set(data, merge=merge)
            elif op == "update":
                ref.update(payload)
            elif op == "delete":
                ref.delete()
        self._ops.clear()

    def _rollback(self) -> None:
        self._ops.clear()

    def set(self, ref: FakeDocumentReference, data: Dict[str, Any], merge: bool = False) -> None:
        self._ops.append(("set", ref, (data, merge)))

    def update(self, ref: FakeDocumentReference, fields: Dict[str, Any]) -> None:
        self._ops.append(("update", ref, fields))

    def delete(self, ref: FakeDocumentReference) -> None:
        self._ops.append(("delete", ref, None))


class FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, name)

    def get_all(self, refs: List[FakeDocumentReference]) -> List[FakeDocumentSnapshot]:
        return [ref.get() for ref in refs]

    def batch(self) -> FakeWriteBatch:
        return FakeWriteBatch()

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


# =========================================================================
# END-TO-END FIRESTORE + BLOB STORAGE VERIFICATION
# =========================================================================


def test_firestore_and_blob_storage_end_to_end() -> None:
    settings = get_settings()
    orig_db_backend = settings.db_backend
    orig_fs_db = settings.firestore_database
    orig_wm = admin_routes_mod.update_section_working_memory
    admin_routes_mod.update_section_working_memory = lambda *args, **kwargs: None

    fake_fs = FakeFirestoreClient()

    with tempfile.TemporaryDirectory() as tmp_blob_dir:
        temp_blobs = LocalBlobStorage(root_dir=Path(tmp_blob_dir))

        reset_db_state()
        settings.db_backend = "firestore"
        settings.firestore_database = "smartresearch-db"
        set_firestore_client_override(fake_fs)
        set_blob_storage_override(temp_blobs)
        reset_blob_storage_cache()

        try:
            init_db(force=True)
            admin_client = TestClient(app)

            # 1. Health check reports firestore dialect
            r_health = admin_client.get("/api/health")
            assert r_health.status_code == 200, r_health.text
            health = r_health.json()
            assert health["database_dialect"] == "firestore", health
            assert health["db_backend"] == "firestore", health

            # 2. Bootstrap admin login + /api/auth/me + starter license plans seeded
            login_as_admin(admin_client)
            r_me_adm = admin_client.get("/api/auth/me")
            assert r_me_adm.status_code == 200, r_me_adm.text
            assert r_me_adm.json()["role"] == "admin"

            r_plans = admin_client.get("/api/admin/plans")
            assert r_plans.status_code == 200, r_plans.text
            plans = r_plans.json()
            assert len(plans) >= 2, f"Expected >=2 seeded starter plans, got {plans}"

            # 3. Create custom plan + student + verify duplicate email HTTP 409 lock
            suffix = uuid4().hex[:6]
            r_new_plan = admin_client.post(
                "/api/admin/plans",
                json={
                    "name": f"Plan Firestore {suffix}",
                    "description": "Plan test Cloud Firestore Enterprise",
                    "price_fcfa": 20000,
                    "module1_days": 30,
                    "module2_days": 30,
                    "module3_days": 90,
                    "module4_days": 30,
                    "ai_quota_included": 50,
                    "is_active": True,
                },
            )
            assert r_new_plan.status_code == 200, r_new_plan.text
            plan_id = r_new_plan.json()["id"]

            stu_email = f"fs_student_{suffix}@smartresearch.test"
            r_stu = admin_client.post(
                "/api/admin/users",
                json={
                    "email": stu_email,
                    "full_name": "Etudiant Firestore Test",
                    "matricule": f"22FS{suffix}",
                    "phone": "690112233",
                    "role": "student",
                    "plan_id": plan_id,
                    "initial_protocol_title": "Protocole Test Firestore + Cloud Storage",
                    "amount_paid_fcfa": 20000,
                    "payment_reference": f"OM-{suffix}",
                },
            )
            assert r_stu.status_code == 200, r_stu.text
            stu_resp = r_stu.json()
            stu_id = stu_resp["user"]["id"]
            proj_id = stu_resp["created_project_id"]
            temp_pw = stu_resp["temporary_password"]
            assert proj_id is not None

            # Duplicate email must fail with 409 via user_emails lock
            r_dup = admin_client.post(
                "/api/admin/users",
                json={
                    "email": stu_email.upper(),
                    "full_name": "Doublon Test",
                    "role": "student",
                },
            )
            assert r_dup.status_code == 409, r_dup.text

            # 4. Student login -> password change -> single-session revocation
            stu_client_1 = TestClient(app)
            r_login_1 = stu_client_1.post(
                "/api/auth/login",
                json={"email": stu_email, "password": temp_pw},
            )
            assert r_login_1.status_code == 200, r_login_1.text
            stu_client_1.headers["Authorization"] = f"Bearer {r_login_1.json()['access_token']}"

            r_chg = stu_client_1.post(
                "/api/auth/change-password",
                json={"current_password": temp_pw, "new_password": "FirestorePass2026!"},
            )
            assert r_chg.status_code == 200, r_chg.text
            assert r_chg.json()["must_change_password"] is False
            stu_client_1.headers["Authorization"] = f"Bearer {r_chg.json()['access_token']}"

            # Second login from another device revokes client 1
            stu_client_2 = TestClient(app)
            r_login_2 = stu_client_2.post(
                "/api/auth/login",
                json={"email": stu_email, "password": "FirestorePass2026!"},
            )
            assert r_login_2.status_code == 200, r_login_2.text
            stu_client_2.headers["Authorization"] = f"Bearer {r_login_2.json()['access_token']}"

            r_revoked = stu_client_1.get("/api/auth/me")
            assert r_revoked.status_code == 401, r_revoked.text
            assert r_revoked.json()["detail"]["code"] == "SESSION_REVOKED"

            r_me_2 = stu_client_2.get("/api/auth/me")
            assert r_me_2.status_code == 200, r_me_2.text
            assert r_me_2.json()["project_id"] == proj_id

            # 5. >1.5 MiB project payload test: metadata in Firestore < 4 KB, JSON in BlobStorage
            proj = project_storage.get(proj_id)
            assert proj is not None
            heavy_text = "Donnees cliniques FMSB CAMES - " * 55000  # > 1.6 MiB UTF-8
            proj.sections["intro_contexte"].content_markdown = heavy_text
            proj.sections["intro_contexte"].content_html = f"<p>{heavy_text}</p>"
            project_storage.save(proj)

            raw_fs_proj = fake_fs._collections["projects"][proj_id]
            assert "data_json" not in raw_fs_proj, "Project data_json must NOT be stored in Firestore!"
            assert raw_fs_proj.get("blob_key"), "Firestore project doc must store blob_key"
            fs_doc_bytes = len(json.dumps(raw_fs_proj, default=str).encode("utf-8"))
            assert fs_doc_bytes < 4096, f"Firestore metadata doc too large: {fs_doc_bytes} bytes"

            blob_bytes = temp_blobs.download_bytes(raw_fs_proj["blob_key"])
            assert blob_bytes is not None and len(blob_bytes) > 1_500_000, (
                f"Expected >1.5 MiB blob, got {len(blob_bytes) if blob_bytes else 0}"
            )

            rehydrated = project_storage.get(proj_id)
            assert rehydrated is not None
            assert rehydrated.sections["intro_contexte"].content_markdown == heavy_text

            # 6. Presentation save/get stores JSON in BlobStorage and metadata < 2 KB in Firestore
            deck = PresentationDeck(
                project_id=proj_id,
                slides=[Slide(id="s1", slide_index=1, title="Titre Test", subtitle="Sous-titre")],
            )
            project_storage.save_presentation(deck)
            raw_fs_pres = fake_fs._collections["presentations"][proj_id]
            assert "data_json" not in raw_fs_pres
            assert len(json.dumps(raw_fs_pres, default=str).encode("utf-8")) < 2048
            loaded_deck = project_storage.get_presentation(proj_id)
            assert loaded_deck is not None and loaded_deck.slides[0].title == "Titre Test"

            # 7. AI quota debit + daily Gemini 3.8 check + admin recharge + entitlement adjust/revoke
            store = get_store()
            try:
                stu_user = store.get_user(stu_id)
                assert stu_user is not None
                record_usage_and_debit(
                    store,
                    stu_user,
                    module_key="module1",
                    action="draft_section",
                    credits_cost=2,
                    project_id=proj_id,
                    model_used="gemini-3.8-flash",
                    status="ok",
                )
                daily_38 = get_student_daily_38_usage(store, stu_user)
                assert daily_38["used_today"] == 1
                assert store.get_quota(stu_id).credits_used == 2  # type: ignore[union-attr]
            finally:
                store.close()

            r_rech = admin_client.post(
                f"/api/admin/users/{stu_id}/recharge-ai",
                json={"add_credits": 25, "daily_38_limit": 5, "notes": "Bonus test"},
            )
            assert r_rech.status_code == 200, r_rech.text
            assert r_rech.json()["ai_quota"]["credits_total"] == 75
            assert r_rech.json()["ai_quota"]["daily_38_limit"] == 5

            r_adj = admin_client.patch(
                f"/api/admin/users/{stu_id}/entitlements/module2",
                json={"revoke": True, "notes": "Test revocation"},
            )
            assert r_adj.status_code == 200, r_adj.text
            assert r_adj.json()["entitlements"]["module2"]["is_active"] is False

            # 8. Admin dashboard KPIs, student stats, usage events, audit logs
            r_dash = admin_client.get("/api/admin/dashboard")
            assert r_dash.status_code == 200, r_dash.text
            dash = r_dash.json()
            assert dash["total_students"] >= 1
            assert dash["total_projects"] >= 1
            assert dash["total_revenue_fcfa"] >= 20000

            r_stats = admin_client.get("/api/admin/stats/students", params={"search": stu_email})
            assert r_stats.status_code == 200, r_stats.text
            assert len(r_stats.json()) == 1

            r_ev = admin_client.get("/api/admin/usage-events", params={"user_id": stu_id})
            assert r_ev.status_code == 200, r_ev.text
            assert len(r_ev.json()) >= 1

            r_aud = admin_client.get("/api/admin/audit-logs")
            assert r_aud.status_code == 200, r_aud.text
            assert len(r_aud.json()) >= 3

            # 9. Admin deletes student -> cascade delete Firestore docs + BlobStorage JSON files
            proj_blob_key = build_object_key("projects", proj_id, "project.json")
            pres_blob_key = build_object_key("presentations", proj_id, "deck.json")
            assert temp_blobs.download_bytes(proj_blob_key) is not None
            assert temp_blobs.download_bytes(pres_blob_key) is not None

            r_del = admin_client.delete(f"/api/admin/users/{stu_id}")
            assert r_del.status_code == 200, r_del.text
            assert r_del.json()["deleted"] is True

            assert stu_id not in fake_fs._collections.get("users", {})
            assert proj_id not in fake_fs._collections.get("projects", {})
            assert proj_id not in fake_fs._collections.get("presentations", {})
            assert stu_id not in fake_fs._collections.get("user_ai_quotas", {})
            assert not any(
                (v.get("user_id") == stu_id)
                for v in fake_fs._collections.get("user_emails", {}).values()
            )
            assert not any(
                (v.get("user_id") == stu_id)
                for v in fake_fs._collections.get("usage_events", {}).values()
            )
            assert temp_blobs.download_bytes(proj_blob_key) is None
            assert temp_blobs.download_bytes(pres_blob_key) is None
        finally:
            admin_routes_mod.update_section_working_memory = orig_wm
            settings.db_backend = orig_db_backend
            settings.firestore_database = orig_fs_db
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()


def test_sql_mode_end_to_end_regression() -> None:
    """Verify DB_BACKEND=sql (SQLite) REST API + raw SessionLocal() backwards-compatibility."""
    settings = get_settings()
    orig_db_backend = settings.db_backend
    orig_wm = admin_routes_mod.update_section_working_memory
    admin_routes_mod.update_section_working_memory = lambda *args, **kwargs: None

    reset_db_state()
    settings.db_backend = "sql"
    set_firestore_client_override(None)
    set_blob_storage_override(None)
    reset_blob_storage_cache()

    created_stu_id: Optional[str] = None
    created_raw_uid: Optional[str] = None
    created_plan_id: Optional[str] = None

    try:
        init_db(force=True)
        admin_client = TestClient(app)

        # 1. Health check reports sqlite / sql
        r_health = admin_client.get("/api/health")
        assert r_health.status_code == 200, r_health.text
        health = r_health.json()
        assert health["database_dialect"] == "sqlite", health
        assert health["db_backend"] == "sql", health

        # 2. Admin login + plan CRUD
        login_as_admin(admin_client)
        suffix = uuid4().hex[:6]
        r_plan = admin_client.post(
            "/api/admin/plans",
            json={
                "name": f"Plan SQL Reg {suffix}",
                "description": "Regression test SQLite",
                "price_fcfa": 15000,
                "module1_days": 30,
                "module2_days": 30,
                "module3_days": 90,
                "module4_days": 30,
                "ai_quota_included": 40,
                "is_active": True,
            },
        )
        assert r_plan.status_code == 200, r_plan.text
        created_plan_id = r_plan.json()["id"]

        # 3. Student creation with locked cover + duplicate email 409
        stu_email = f"sql_reg_{suffix}@smartresearch.test"
        r_stu = admin_client.post(
            "/api/admin/users",
            json={
                "email": stu_email,
                "full_name": "Etudiant SQL Regression",
                "matricule": f"22SQ{suffix}",
                "phone": "699112233",
                "role": "student",
                "plan_id": created_plan_id,
                "initial_protocol_title": "Protocole SQLite Regression",
                "amount_paid_fcfa": 15000,
                "payment_reference": f"SQL-{suffix}",
            },
        )
        assert r_stu.status_code == 200, r_stu.text
        stu_payload = r_stu.json()
        created_stu_id = stu_payload["user"]["id"]
        proj_id = stu_payload["created_project_id"]
        temp_pw = stu_payload["temporary_password"]
        assert proj_id is not None and stu_payload["user"]["topic_locked"] is True

        r_dup = admin_client.post(
            "/api/admin/users",
            json={"email": stu_email.upper(), "full_name": "Doublon SQL", "role": "student"},
        )
        assert r_dup.status_code == 409, r_dup.text

        # 4. Raw SessionLocal() direct calls to service functions
        raw_db = SessionLocal()
        try:
            admin_orm = raw_db.query(UserORM).filter(UserORM.role == "admin").first()
            plan_orm = raw_db.get(LicensePlanORM, created_plan_id)
            assert admin_orm is not None and plan_orm is not None
            raw_user = create_user_account(
                raw_db,
                email=f"raw_sql_{suffix}@smartresearch.test",
                full_name="Raw Session User",
                password="RawUserPass2026!",
                role="student",
                must_change_password=False,
            )
            created_raw_uid = raw_user.id
            apply_license_plan_to_user(raw_db, admin_orm, raw_user, plan_orm, notes="Raw session grant")
            daily = get_student_daily_38_usage(raw_db, raw_user)
            assert daily["used_today"] == 0
        finally:
            raw_db.close()

        # 5. Student login + password change + single-session revocation
        c1 = TestClient(app)
        r_l1 = c1.post("/api/auth/login", json={"email": stu_email, "password": temp_pw})
        assert r_l1.status_code == 200, r_l1.text
        c1.headers["Authorization"] = f"Bearer {r_l1.json()['access_token']}"

        r_cp = c1.post(
            "/api/auth/change-password",
            json={"current_password": temp_pw, "new_password": "SqlPassChanged2026!"},
        )
        assert r_cp.status_code == 200 and r_cp.json()["must_change_password"] is False
        c1.headers["Authorization"] = f"Bearer {r_cp.json()['access_token']}"

        c2 = TestClient(app)
        r_l2 = c2.post(
            "/api/auth/login",
            json={"email": stu_email, "password": "SqlPassChanged2026!"},
        )
        assert r_l2.status_code == 200, r_l2.text
        c2.headers["Authorization"] = f"Bearer {r_l2.json()['access_token']}"

        r_rev = c1.get("/api/auth/me")
        assert r_rev.status_code == 401 and r_rev.json()["detail"]["code"] == "SESSION_REVOKED"
        assert c2.get("/api/auth/me").status_code == 200

        # 6. Project & presentation save/get via project_storage
        proj = project_storage.get(proj_id)
        assert proj is not None
        proj.sections["intro_contexte"].content_markdown = "Contexte valide SQLite."
        project_storage.save(proj)
        assert project_storage.get(proj_id).sections["intro_contexte"].content_markdown == "Contexte valide SQLite."  # type: ignore[union-attr]

        deck = PresentationDeck(
            project_id=proj_id,
            slides=[Slide(id="s1", slide_index=1, title="Slide SQL", subtitle="Sub")],
        )
        project_storage.save_presentation(deck)
        loaded_deck = project_storage.get_presentation(proj_id)
        assert loaded_deck is not None and loaded_deck.slides[0].title == "Slide SQL"

        # 7. AI quota debit + recharge + entitlement revoke
        store = get_store()
        try:
            stu_rec = store.get_user(created_stu_id)
            assert stu_rec is not None
            record_usage_and_debit(
                store,
                stu_rec,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                project_id=proj_id,
                model_used="gemini-3.8-flash",
                status="ok",
            )
            assert get_student_daily_38_usage(store, stu_rec)["used_today"] == 1
        finally:
            store.close()

        r_rech = admin_client.post(
            f"/api/admin/users/{created_stu_id}/recharge-ai",
            json={"add_credits": 10, "daily_38_limit": 4, "notes": "SQL recharge"},
        )
        assert r_rech.status_code == 200 and r_rech.json()["ai_quota"]["credits_total"] == 50

        r_ent = admin_client.patch(
            f"/api/admin/users/{created_stu_id}/entitlements/module2",
            json={"revoke": True, "notes": "SQL revoke"},
        )
        assert r_ent.status_code == 200 and r_ent.json()["entitlements"]["module2"]["is_active"] is False

        # 8. Admin dashboard, stats, usage-events, audit-logs
        assert admin_client.get("/api/admin/dashboard").status_code == 200
        assert len(admin_client.get("/api/admin/stats/students", params={"search": stu_email}).json()) == 1
        assert len(admin_client.get("/api/admin/usage-events", params={"user_id": created_stu_id}).json()) >= 1
        assert len(admin_client.get("/api/admin/audit-logs").json()) >= 1

        # 9. Cascade delete via API for student
        r_del = admin_client.delete(f"/api/admin/users/{created_stu_id}")
        assert r_del.status_code == 200 and r_del.json()["deleted"] is True
        created_stu_id = None
    finally:
        cleanup_db = SessionLocal()
        try:
            for uid in (created_stu_id, created_raw_uid):
                if uid:
                    cleanup_db.query(AdminAuditLogORM).filter(AdminAuditLogORM.target_user_id == uid).delete()
                    u = cleanup_db.get(UserORM, uid)
                    if u is not None:
                        cleanup_db.delete(u)
            if created_plan_id:
                p = cleanup_db.get(LicensePlanORM, created_plan_id)
                if p is not None:
                    cleanup_db.delete(p)
            cleanup_db.commit()
        finally:
            cleanup_db.close()

        admin_routes_mod.update_section_working_memory = orig_wm
        settings.db_backend = orig_db_backend
        set_firestore_client_override(None)
        set_blob_storage_override(None)
        reset_blob_storage_cache()
        reset_db_state()


def _run_dual_auth_checks(admin_client: TestClient) -> List[str]:
    """Create username-only & duplicate-name users, test all login identifiers, return created IDs."""
    created_ids: List[str] = []
    suffix = uuid4().hex[:6]

    # 1. Admin can also sign in using full_name (and email keeps working)
    login_as_admin(admin_client)
    me_admin = admin_client.get("/api/auth/me")
    assert me_admin.status_code == 200, me_admin.text
    admin_full_name = me_admin.json()["full_name"]
    admin_email = me_admin.json()["email"]
    admin_password = get_settings().bootstrap_admin_password

    auth_probe = TestClient(app)
    r_adm_name = auth_probe.post(
        "/api/auth/login",
        json={"identifier": f"  {admin_full_name.upper()}  ", "password": admin_password},
    )
    assert r_adm_name.status_code == 200, r_adm_name.text
    assert r_adm_name.json()["user"]["email"] == admin_email

    # Re-login admin_client because active_session_id rotated
    login_as_admin(admin_client)

    # 2. Create student with plain username (no '@') + initial protocol cover
    plain_username = f"jean.kamga.{suffix}"
    plain_full_name = f"Jean Kamga {suffix}"
    r_u1 = admin_client.post(
        "/api/admin/users",
        json={
            "email": plain_username,
            "full_name": plain_full_name,
            "matricule": f"20M{suffix}",
            "role": "student",
            "initial_protocol_title": f"Protocole Sans Email {suffix}",
        },
    )
    assert r_u1.status_code == 200, r_u1.text
    u1_data = r_u1.json()
    u1_id = u1_data["user"]["id"]
    u1_pw = u1_data["temporary_password"]
    u1_pid = u1_data["created_project_id"]
    created_ids.append(u1_id)
    assert u1_data["user"]["email"] == plain_username.lower()

    # Plain username must NOT be copied into cover.student_email
    assert u1_pid, u1_data
    created_proj = project_storage.get(u1_pid)
    assert created_proj is not None
    assert created_proj.cover.student_email == "", (
        f"Expected empty cover.student_email for username-only user, got {created_proj.cover.student_email!r}"
    )

    # Login via plain username (email field), mixed-case/extra-spaces full_name, and username field
    for payload in (
        {"email": plain_username, "password": u1_pw},
        {"email": f"  jEaN   kAmGa   {suffix}  ", "password": u1_pw},
        {"username": plain_username, "password": u1_pw},
    ):
        r_login = auth_probe.post("/api/auth/login", json=payload)
        assert r_login.status_code == 200, f"Login failed for {payload}: {r_login.text}"
        assert r_login.json()["user"]["id"] == u1_id

    # Invalid password still yields 401 INVALID_CREDENTIALS
    r_bad_pw = auth_probe.post("/api/auth/login", json={"email": plain_username, "password": "WrongPassword!"})
    assert r_bad_pw.status_code == 401
    assert r_bad_pw.json()["detail"]["code"] == "INVALID_CREDENTIALS"

    # Too-short identifier yields 422
    r_short = auth_probe.post("/api/auth/login", json={"email": "x", "password": u1_pw})
    assert r_short.status_code == 422

    # 3. Duplicate full_name ambiguity check
    dup_name = f"Paul Duplicate {suffix}"
    dup_users = []
    for idx in (1, 2):
        uname = f"paul.dup{idx}.{suffix}"
        r_dup = admin_client.post(
            "/api/admin/users",
            json={
                "email": uname,
                "full_name": dup_name,
                "role": "student",
            },
        )
        assert r_dup.status_code == 200, r_dup.text
        du = r_dup.json()
        created_ids.append(du["user"]["id"])
        dup_users.append((uname, du["user"]["id"], du["temporary_password"]))

    r_ambig = auth_probe.post("/api/auth/login", json={"email": dup_name, "password": dup_users[0][2]})
    assert r_ambig.status_code == 401, r_ambig.text
    assert r_ambig.json()["detail"]["code"] == "AMBIGUOUS_USERNAME", r_ambig.json()

    # Each ambiguous-name account can still sign in using its unique username
    for uname, uid, pw in dup_users:
        r_ok = auth_probe.post("/api/auth/login", json={"username": uname, "password": pw})
        assert r_ok.status_code == 200, r_ok.text
        assert r_ok.json()["user"]["id"] == uid

    return created_ids


def test_dual_auth_modes() -> None:
    """Verify username, full_name (incl. ambiguity), and email login in both Firestore and SQLite."""
    settings = get_settings()
    orig_db_backend = settings.db_backend
    orig_fs_db = settings.firestore_database
    orig_wm = admin_routes_mod.update_section_working_memory
    admin_routes_mod.update_section_working_memory = lambda *args, **kwargs: None

    # --- A. Firestore mode ---
    fake_fs = FakeFirestoreClient()
    with tempfile.TemporaryDirectory() as tmp_blob_dir:
        temp_blobs = LocalBlobStorage(root_dir=Path(tmp_blob_dir))
        reset_db_state()
        settings.db_backend = "firestore"
        settings.firestore_database = "smartresearch-db"
        set_firestore_client_override(fake_fs)
        set_blob_storage_override(temp_blobs)
        reset_blob_storage_cache()
        try:
            init_db(force=True)
            fs_client = TestClient(app)
            _run_dual_auth_checks(fs_client)
        finally:
            settings.db_backend = orig_db_backend
            settings.firestore_database = orig_fs_db
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()

    # --- B. SQLite mode ---
    reset_db_state()
    settings.db_backend = "sql"
    set_firestore_client_override(None)
    set_blob_storage_override(None)
    reset_blob_storage_cache()
    sql_created_ids: List[str] = []
    try:
        init_db(force=True)
        sql_client = TestClient(app)
        sql_created_ids = _run_dual_auth_checks(sql_client)
        for uid in list(sql_created_ids):
            r_del = sql_client.delete(f"/api/admin/users/{uid}")
            assert r_del.status_code == 200, r_del.text
            sql_created_ids.remove(uid)
    finally:
        if sql_created_ids:
            cleanup_db = SessionLocal()
            try:
                for uid in sql_created_ids:
                    cleanup_db.query(AdminAuditLogORM).filter(AdminAuditLogORM.target_user_id == uid).delete()
                    u = cleanup_db.get(UserORM, uid)
                    if u is not None:
                        cleanup_db.delete(u)
                cleanup_db.commit()
            finally:
                cleanup_db.close()
        admin_routes_mod.update_section_working_memory = orig_wm
        settings.db_backend = orig_db_backend
        settings.firestore_database = orig_fs_db
        set_firestore_client_override(None)
        set_blob_storage_override(None)
        reset_blob_storage_cache()
        reset_db_state()


def _verify_rate_limits_in_store() -> Optional[str]:
    from datetime import timedelta
    from fastapi import HTTPException
    from app.db.database import get_store
    from app.services.auth_service import create_user_account
    from app.services.licensing_service import assert_ai_quota_available

    settings = get_settings()
    orig_rpm = settings.ai_user_max_rpm
    orig_rpd = settings.ai_user_max_rpd
    settings.ai_user_max_rpm = 2
    settings.ai_user_max_rpd = 3

    store = get_store()
    created_uid: Optional[str] = None
    try:
        suffix = uuid4().hex[:6]
        stu = create_user_account(
            db=store,
            email=f"rl.stu.{suffix}",
            full_name=f"Rate Limit Student {suffix}",
            password="TempPassword123!",
            role="student",
        )
        created_uid = stu.id
        now = datetime.now(timezone.utc)
        store.upsert_entitlement(
            user_id=stu.id,
            module_key="module1",
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            updated_at=now,
        )
        store.add_quota_credits_atomic(user_id=stu.id, add_credits=100, now=now)

        # 2 calls within the last minute -> 3rd call hits RPM (429 AI_RATE_LIMIT_RPM)
        for _ in range(2):
            record_usage_and_debit(
                store,
                stu,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.8-flash",
                status="ok",
            )

        try:
            assert_ai_quota_available(store, stu, module_key="module1", action="draft_section")
            raise AssertionError("Expected HTTP 429 AI_RATE_LIMIT_RPM")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.detail
            assert exc.detail["code"] == "AI_RATE_LIMIT_RPM", exc.detail

        # Raise RPM to 10 so RPD=3 is tested next; add 1 more event -> 4th call hits RPD (429 AI_RATE_LIMIT_RPD)
        settings.ai_user_max_rpm = 10
        assert_ai_quota_available(store, stu, module_key="module1", action="draft_section")
        record_usage_and_debit(
            store,
            stu,
            module_key="module1",
            action="audit_section",
            credits_cost=1,
            model_used="gemini-3.8-flash",
            status="ok",
        )
        try:
            assert_ai_quota_available(store, stu, module_key="module1", action="audit_section")
            raise AssertionError("Expected HTTP 429 AI_RATE_LIMIT_RPD for student")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.detail
            assert exc.detail["code"] == "AI_RATE_LIMIT_RPD", exc.detail

        store.delete_user_cascade(stu.id)
        created_uid = None

        # Verify rate limits apply strictly to ADMINISTRATOR profiles as well
        settings.ai_user_max_rpm = 2
        settings.ai_user_max_rpd = 3
        adm = create_user_account(
            db=store,
            email=f"rl.adm.{suffix}",
            full_name=f"Rate Limit Admin {suffix}",
            password="TempPassword123!",
            role="admin",
        )
        created_uid = adm.id

        for _ in range(2):
            record_usage_and_debit(
                store,
                adm,
                module_key="module1",
                action="draft_section",
                credits_cost=2,
                model_used="gemini-3.8-flash",
                status="ok",
            )

        try:
            assert_ai_quota_available(store, adm, module_key="module1", action="draft_section")
            raise AssertionError("Expected HTTP 429 AI_RATE_LIMIT_RPM for admin")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.detail
            assert exc.detail["code"] == "AI_RATE_LIMIT_RPM", exc.detail

        settings.ai_user_max_rpm = 10
        assert_ai_quota_available(store, adm, module_key="module1", action="draft_section")
        record_usage_and_debit(
            store,
            adm,
            module_key="module1",
            action="audit_section",
            credits_cost=1,
            model_used="gemini-3.8-flash",
            status="ok",
        )
        try:
            assert_ai_quota_available(store, adm, module_key="module1", action="audit_section")
            raise AssertionError("Expected HTTP 429 AI_RATE_LIMIT_RPD for admin")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.detail
            assert exc.detail["code"] == "AI_RATE_LIMIT_RPD", exc.detail

        store.delete_user_cascade(adm.id)
        created_uid = None
        return None
    finally:
        settings.ai_user_max_rpm = orig_rpm
        settings.ai_user_max_rpd = orig_rpd
        store.close()
    return created_uid


def test_user_rpm_and_rpd_rate_limits() -> None:
    """Verify AI_USER_MAX_RPM (429) and AI_USER_MAX_RPD (429) enforcement in Firestore and SQL."""
    settings = get_settings()
    orig_db_backend = settings.db_backend
    orig_fs_db = settings.firestore_database

    fake_fs = FakeFirestoreClient()
    with tempfile.TemporaryDirectory() as tmp_blob_dir:
        temp_blobs = LocalBlobStorage(root_dir=Path(tmp_blob_dir))
        reset_db_state()
        settings.db_backend = "firestore"
        settings.firestore_database = "smartresearch-db"
        set_firestore_client_override(fake_fs)
        set_blob_storage_override(temp_blobs)
        reset_blob_storage_cache()
        try:
            init_db(force=True)
            _verify_rate_limits_in_store()
        finally:
            settings.db_backend = orig_db_backend
            settings.firestore_database = orig_fs_db
            set_firestore_client_override(None)
            set_blob_storage_override(None)
            reset_blob_storage_cache()
            reset_db_state()

    reset_db_state()
    settings.db_backend = "sql"
    try:
        init_db(force=True)
        _verify_rate_limits_in_store()
    finally:
        settings.db_backend = orig_db_backend
        settings.firestore_database = orig_fs_db
        reset_db_state()


if __name__ == "__main__":
    test_firestore_and_blob_storage_end_to_end()
    print("[OK] test_firestore_and_blob_storage_end_to_end")
    test_sql_mode_end_to_end_regression()
    print("[OK] test_sql_mode_end_to_end_regression")
    test_dual_auth_modes()
    print("[OK] test_dual_auth_modes")
    test_user_rpm_and_rpd_rate_limits()
    print("[OK] test_user_rpm_and_rpd_rate_limits")
    print("ALL DUAL-MODE FIRESTORE + SQL TESTS PASSED")
