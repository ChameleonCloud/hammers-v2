from datetime import timedelta as TimeDelta
from unittest import mock

import pytest
from freezegun import freeze_time
from openstack.network.v2.floating_ip import FloatingIP
from openstack.network.v2.port import Port
from openstack.network.v2.router import Router

from hammers import network_ip_cleaner

TIME_NOW = "2024-10-30T12:13:14Z"

# we should be mocking our time comparison everywhere we can
FAKE_GRACE_PERIOD = TimeDelta(days=9999)


def test_parse_args_default():
    args = ["--cloud", "foo"]
    result = network_ip_cleaner.parse_args(args)
    assert result.cloud == "foo"
    assert result.grace_days == 7
    assert not result.dry_run


def test_parse_args_dry_run():
    args = ["--cloud", "foo", "--dry-run"]
    result = network_ip_cleaner.parse_args(args)
    assert result.dry_run


def test_parse_args_grace_days():
    args = ["--cloud", "foo", "--grace-days", "27"]
    result = network_ip_cleaner.parse_args(args)
    assert result.grace_days == 27


@freeze_time(TIME_NOW)
def test_grace_period_expired():
    TIME_RECENT = "2024-10-28T12:13:14Z"
    TIME_OLD = "2023-10-30T12:13:14Z"
    GRACE_PERIOD_SHORT = TimeDelta(days=1)  # TIME_RECENT should expire
    GRACE_PERIOD_LONG = TimeDelta(days=7)  # TIME_RECENT should not expire
    assert network_ip_cleaner.grace_period_expired(TIME_RECENT, GRACE_PERIOD_SHORT)
    assert not network_ip_cleaner.grace_period_expired(TIME_RECENT, GRACE_PERIOD_LONG)
    assert network_ip_cleaner.grace_period_expired(TIME_OLD, GRACE_PERIOD_SHORT)
    assert network_ip_cleaner.grace_period_expired(TIME_OLD, GRACE_PERIOD_LONG)


class TestFindFLoatingIPs:
    conn = mock.patch("openstack.connection.Connection")
    conn.list_floating_ips = mock.MagicMock()

    @mock.patch.object(network_ip_cleaner, "grace_period_expired")
    @pytest.mark.parametrize(
        "fip_addr,fixed_addr,status,is_grace_exp,tag,should_delete",
        [
            ("1.2.3.4", "192.168.1.1", "ACTIVE", False, None, False),
            ("1.2.3.4", None, "DOWN", False, None, False),
            ("1.2.3.4", "192.168.1.1", "ACTIVE", False, "blazar", False),
            ("1.2.3.4", None, "DOWN", False, "blazar", False),
            ("1.2.3.4", "192.168.1.1", "ACTIVE", True, None, False),
            ("1.2.3.4", None, "DOWN", True, None, True),
            ("1.2.3.4", "192.168.1.1", "ACTIVE", True, "blazar", False),
            ("1.2.3.4", None, "DOWN", True, "blazar", False),
        ],
    )
    def test_find_idle_fip(
        self,
        mock_grace_period_expired,
        fip_addr,
        fixed_addr,
        status,
        is_grace_exp,
        tag,
        should_delete,
    ):
        mock_grace_period_expired.return_value = is_grace_exp

        FIP = FloatingIP(
            floating_ip_address=fip_addr,
            fixed_ip_address=fixed_addr,
            status=status,
            tags=[tag],
        )
        self.conn.list_floating_ips.return_value = [FIP]

        result = list(network_ip_cleaner.find_idle_floating_ips(self.conn, FAKE_GRACE_PERIOD))

        if should_delete:
            assert result == [FIP]
        else:
            assert result == []


class TestCleanRouters:
    conn = mock.patch("openstack.connection.Connection")
    conn.list_routers = mock.MagicMock()
    conn.list_router_interfaces = mock.MagicMock()

    @mock.patch.object(network_ip_cleaner, "grace_period_expired")
    @pytest.mark.parametrize(
        "is_grace_exp,should_delete",
        [
            (True, True),
            (False, False),
        ],
    )
    def test_expired(
        self,
        mock_grace_period_expired: mock.MagicMock,
        is_grace_exp,
        should_delete,
    ):
        mock_grace_period_expired.return_value = is_grace_exp

        FAKE_ROUTER = Router(name="fake_name")
        self.conn.list_routers.return_value = [FAKE_ROUTER]
        self.conn.list_router_interfaces.return_value = []
        routers_to_delete = list(
            network_ip_cleaner.find_idle_routers(self.conn, grace_period=FAKE_GRACE_PERIOD)
        )

        if should_delete:
            assert routers_to_delete == [FAKE_ROUTER]
        else:
            assert routers_to_delete == []

    @mock.patch.object(network_ip_cleaner, "grace_period_expired")
    def test_with_interfaces(self, mock_grace_period_expired: mock.MagicMock):
        mock_grace_period_expired.return_value = True

        FAKE_ROUTER = Router(name="fake_name", id="fake_id")
        self.conn.list_routers.return_value = [FAKE_ROUTER]

        FAKE_INTERFACE = Port(
            device_id=FAKE_ROUTER.id,
            device_owner="network:router_interface",
        )
        self.conn.list_router_interfaces.return_value = [FAKE_INTERFACE]

        routers_to_delete = list(
            network_ip_cleaner.find_idle_routers(self.conn, grace_period=FAKE_GRACE_PERIOD)
        )

        assert routers_to_delete == []

    @mock.patch.object(network_ip_cleaner, "grace_period_expired")
    def test_has_blazar_ip(self, mock_grace_period_expired: mock.MagicMock):
        mock_grace_period_expired.return_value = True

        self.conn.list_router_interfaces.return_value = []

        FAKE_ROUTER = Router(
            name="fake_name",
            id="fake_id",
            external_gateway_info={
                "external_fixed_ips": [
                    {
                        "subnet_id": "fake-subnet-id",
                        "ip_address": "fake-reservable-ip-address",
                    }
                ],
            },
        )
        self.conn.list_routers.return_value = [FAKE_ROUTER]
        routers_to_delete = list(
            network_ip_cleaner.find_idle_routers(
                self.conn,
                grace_period=FAKE_GRACE_PERIOD,
                ip_whitelist={"fake-not-reservable-ip-address"},
            )
        )
        assert routers_to_delete == [FAKE_ROUTER]

        routers_to_delete = list(
            network_ip_cleaner.find_idle_routers(
                self.conn,
                grace_period=FAKE_GRACE_PERIOD,
                ip_whitelist={"fake-reservable-ip-address"},
            )
        )
        assert routers_to_delete == []
