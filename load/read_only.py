from locust import HttpUser, task, constant
class ReadOnlyTicketUser(HttpUser):
    wait_time=constant(0.1)
    @task
    def list_tickets(self):
        with self.client.get("/tickets?limit=20&offset=0",name="GET tickets list",timeout=10,catch_response=True) as response:
            try:
                if response.status_code!=200 or not isinstance(response.json(),list):response.failure("List contract failed")
            except ValueError:response.failure("Invalid JSON")
