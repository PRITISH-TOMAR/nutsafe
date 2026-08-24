from storageBundle.tenants import get_by_api_key


def authenticate(api_key: str) -> int | None:
    """
    Resolve an api-key to a tenant_id.
    Returns None if the key is missing, unknown, or inactive.
    """
    if not api_key:
        return None
    row = get_by_api_key(api_key)
    return row["tenant_id"] if row else None
