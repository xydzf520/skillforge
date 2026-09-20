"""M4 worker registry tests."""

import pytest


@pytest.mark.asyncio
async def test_register_list_and_heartbeat_worker(client):
  create = await client.post("/api/executions/workers/register", json={
      "id": "worker-a",
      "name": "Worker A",
      "queue_name": "default",
      "capacity": 4,
      "active_runs": 1,
      "metadata": {"zone": "cn-sh"},
  })
  assert create.status_code == 200
  assert create.json()["id"] == "worker-a"

  heartbeat = await client.post("/api/executions/workers/worker-a/heartbeat", json={
      "active_runs": 2,
      "status": "online",
  })
  assert heartbeat.status_code == 200
  assert heartbeat.json()["active_runs"] == 2

  listed = await client.get("/api/executions/workers")
  assert listed.status_code == 200
  assert listed.json()["total"] >= 1
