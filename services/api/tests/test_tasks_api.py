import asyncio
from types import SimpleNamespace
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.auth import provision_user
from app.core.database import Base, _enable_sqlite_foreign_keys, get_db
from app.main import app
from app.models.domain import ActionProposal, AnticipationInsight, Task

@pytest.fixture
async def harness(tmp_path):
    engine=create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}")
    event.listen(engine.sync_engine,"connect",_enable_sqlite_foreign_keys)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,class_=AsyncSession,expire_on_commit=False)
    async def override_db():
        async with factory() as db: yield db
    app.dependency_overrides[get_db]=override_db
    async with factory() as db: session=await provision_user(db)
    async with factory() as db: second_session=await provision_user(db)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        yield SimpleNamespace(
            client=client,
            factory=factory,
            token=session[1],
            owner_id=session[0].id,
            second_token=second_session[1],
        )
    app.dependency_overrides.pop(get_db,None); await engine.dispose()

def auth(h): return {"Authorization":f"Bearer {h.token}"}

@pytest.mark.asyncio
async def test_task_crud_and_owner_scoped_action_approval(harness):
    created=await harness.client.post("/api/v1/tasks",json={"title":"Prepare ORHEX demo","priority":"high"},headers=auth(harness))
    assert created.status_code==201,created.text
    task_id=created.json()["id"]
    completed=await harness.client.patch(f"/api/v1/tasks/{task_id}",json={"status":"completed"},headers=auth(harness))
    assert completed.status_code==200 and completed.json()["completed_at"]
    async with harness.factory() as db:
        owner_id=(await db.scalar(select(Task).where(Task.id==task_id))).user_id
        db.add(AnticipationInsight(user_id=owner_id,fingerprint="f"*64,kind="commitment_due_soon",source_type="commitment",source_id="c1",title="Submit report",explanation="A commitment is due soon.",priority="medium",status="active"))
        await db.commit()
    prepared=await harness.client.post("/api/v1/tasks/actions/prepare",headers=auth(harness))
    assert prepared.status_code==200 and len(prepared.json())==1
    action_id=prepared.json()[0]["id"]
    approved=await harness.client.post(f"/api/v1/tasks/actions/{action_id}/approve",json={"status":"approved"},headers=auth(harness))
    assert approved.status_code==200 and approved.json()["status"]=="executed"
    repeated=await harness.client.post(f"/api/v1/tasks/actions/{action_id}/approve",json={"status":"approved"},headers=auth(harness))
    assert repeated.status_code==409
    async with harness.factory() as db:
        assert await db.scalar(select(Task).where(Task.title=="Submit report")) is not None
        assert await db.scalar(select(ActionProposal).where(ActionProposal.id==action_id)) is not None


@pytest.mark.asyncio
async def test_concurrent_proposal_approval_executes_once(harness):
    async with harness.factory() as db:
        db.add(ActionProposal(
            user_id=harness.owner_id,
            fingerprint="a" * 64,
            action_type="create_task",
            title="Create approved task",
            rationale="User-approved task",
            payload_json='{"title":"Approved once","priority":"medium"}',
            status="proposed",
        ))
        await db.commit()
        proposal_id = (await db.scalar(
            select(ActionProposal).where(ActionProposal.fingerprint == "a" * 64)
        )).id

    async def approve():
        return await harness.client.post(
            f"/api/v1/tasks/actions/{proposal_id}/approve",
            json={"status": "approved"},
            headers=auth(harness),
        )

    first, second = await asyncio.gather(approve(), approve())
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    async with harness.factory() as db:
        tasks = list((await db.scalars(select(Task).where(
            Task.title == "Approved once",
        ))).all())
        proposal = await db.scalar(select(ActionProposal).where(
            ActionProposal.id == proposal_id,
        ))
        assert len(tasks) == 1
        assert proposal.status == "executed"


@pytest.mark.asyncio
async def test_proposal_approval_is_isolated_between_users(harness):
    async with harness.factory() as db:
        proposal = ActionProposal(
            user_id=harness.owner_id,
            fingerprint="b" * 64,
            action_type="create_task",
            title="Private proposal",
            rationale="Owner-only proposal",
            payload_json='{"title":"Private task"}',
            status="proposed",
        )
        db.add(proposal)
        await db.commit()
        proposal_id = proposal.id

    denied = await harness.client.post(
        f"/api/v1/tasks/actions/{proposal_id}/approve",
        json={"status": "approved"},
        headers={"Authorization": f"Bearer {harness.second_token}"},
    )
    assert denied.status_code == 404
    async with harness.factory() as db:
        proposal = await db.scalar(select(ActionProposal).where(
            ActionProposal.id == proposal_id,
        ))
        assert proposal.status == "proposed"
        assert await db.scalar(select(Task).where(
            Task.title == "Private task",
        )) is None
