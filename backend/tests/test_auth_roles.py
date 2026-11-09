import pytest

from backend.auth_roles import is_admin_account


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ({"is_admin": True}, True),
        ({"role": "admin"}, True),
        ({"role": "Administrator"}, True),
        ({"is_admin": False, "role": "user"}, False),
        ({"is_admin": "true", "role": "user"}, False),
        ({}, False),
    ],
)
def test_admin_access_comes_from_trusted_account_fields(account, expected):
    assert is_admin_account(account) is expected
