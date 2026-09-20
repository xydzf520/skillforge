"""
SkillForge API 负载测试脚本（使用 locust）。
运行: locust -f scripts/loadtest.py --host=http://localhost:8000
"""

from locust import HttpUser, task, between


class SkillForgeUser(HttpUser):
    """模拟普通用户操作"""
    wait_time = between(1, 3)

    def on_start(self):
        """登录获取 session cookie"""
        self.client.post("/api/auth/login", json={
            "username": "admin",
            "password": __import__("os").environ["SKILLFORGE_TEST_PASSWORD"],
        })

    @task(5)
    def list_skills(self):
        self.client.get("/api/skills/?page=1&page_size=20")

    @task(3)
    def get_dashboard(self):
        self.client.get("/api/dashboard/overview?days=7")

    @task(2)
    def list_reviews(self):
        self.client.get("/api/reviews/?page=1&page_size=20")

    @task(2)
    def list_executions(self):
        self.client.get("/api/executions/runs?page=1&page_size=20")

    @task(1)
    def health_check(self):
        self.client.get("/health")

    @task(1)
    def list_datasources(self):
        self.client.get("/api/data-sources/")
