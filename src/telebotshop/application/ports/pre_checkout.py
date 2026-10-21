from collections.abc import Awaitable, Callable

from telebotshop.domain.models.pre_checkout import PreCheckout

PRE_CHECKOUT_DB_BUDGET = 4
type PreCheckoutCheck = Callable[[PreCheckout], Awaitable[bool]]
