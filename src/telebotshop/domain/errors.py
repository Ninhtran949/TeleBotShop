class InvalidInput(ValueError):
    pass


class AccessDenied(Exception):
    pass


class OwnerConfigurationError(Exception):
    pass


class InvalidAction(Exception):
    pass


class CatalogNotFound(Exception):
    pass


class OutOfStock(Exception):
    pass
