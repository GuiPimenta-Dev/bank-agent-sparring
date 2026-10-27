from dataclasses import dataclass
from datetime import datetime

from sparring.bank.domain.customer import CustomerId


@dataclass(frozen=True)
class VerifiedSession:
    customer_id: CustomerId
    expires_at: datetime

    def is_active(self, now: datetime) -> bool:
        return now < self.expires_at
