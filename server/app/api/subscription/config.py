import logging
import os
from enum import Enum

import stripe

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

MONTHLY_PRICE_ID = os.getenv("STRIPE_MONTHLY_PRICE_ID")
YEARLY_PRICE_ID = os.getenv("STRIPE_YEARLY_PRICE_ID")
YOUR_DOMAIN = os.getenv("CLIENT_DOMAIN", "http://localhost:3000")

logger = logging.getLogger(__name__)

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY
else:
    logger.warning("STRIPE_API_KEY is not set. Subscription routes will be disabled.")


class SubscriptionInterval(str, Enum):
    MONTHLY = "month"
    YEARLY = "year"


def is_valid_price_id(price_id: str) -> bool:
    """Check if the price ID is one of our configured prices."""
    return price_id in [MONTHLY_PRICE_ID, YEARLY_PRICE_ID]
