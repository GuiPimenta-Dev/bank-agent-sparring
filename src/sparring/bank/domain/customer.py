from dataclasses import dataclass
from datetime import date
from typing import NewType

CustomerId = NewType("CustomerId", str)


@dataclass(frozen=True)
class IdentityClaim:
    customer_id: CustomerId
    date_of_birth: date
    postcode: str


@dataclass(frozen=True)
class Customer:
    id: CustomerId
    full_name: str
    date_of_birth: date
    postcode: str

    def is_claimed_by(self, claim: IdentityClaim) -> bool:
        return (
            claim.customer_id == self.id
            and claim.date_of_birth == self.date_of_birth
            and _normalized(claim.postcode) == _normalized(self.postcode)
        )


def _normalized(postcode: str) -> str:
    return postcode.replace(" ", "").upper()
