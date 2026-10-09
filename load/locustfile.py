from locust import HttpUser, task, constant
import uuid


class TicketUser(HttpUser):
    wait_time = constant(0.1)

    @task(4)
    def list_tickets(self):
        with self.client.get('/tickets?limit=20&offset=0', name='GET tickets list', timeout=10, catch_response=True) as response:
            try:
                if response.status_code != 200 or not isinstance(response.json(), list):
                    response.failure('List contract failed')
            except ValueError:
                response.failure('Invalid JSON')

    @task(1)
    def create_ticket(self):
        title = 'load-' + uuid.uuid4().hex
        with self.client.post('/tickets', json={'title': title}, name='POST tickets', timeout=10, catch_response=True) as response:
            try:
                data = response.json()
                if response.status_code != 201 or data.get('title') != title or data.get('status') != 'open':
                    response.failure('Create contract failed')
            except (ValueError, AttributeError):
                response.failure('Invalid response shape')
