from unittest import mock

import pytest
from openstack.baremetal.v1._proxy import Proxy as BaremetalProxy
from openstack.baremetal.v1.node import Node

from hammers import serial_console_enabler


def test_parse_args_default():
    args = ["--cloud", "foo"]
    result = serial_console_enabler.parse_args(args)
    assert result.cloud == "foo"
    assert not result.dry_run
    assert not result.debug


def test_parse_args_dry_run():
    args = ["--cloud", "foo", "--dry-run"]
    result = serial_console_enabler.parse_args(args)
    assert result.dry_run


class TestFindNodesMissingConsole:
    @pytest.mark.parametrize(
        "console_enabled,maintenance,console_interface,provision_state,should_yield",
        [
            (False, False, "ipmitool-socat", "active", True),
            (False, False, "ipmitool-socat", "available", True),
            (False, False, "ipmitool-socat", "manageable", True),
            (True, False, "ipmitool-socat", "active", False),
            (False, True, "ipmitool-socat", "active", False),
            (False, False, "no-console", "active", False),
            (False, False, "ipmitool-socat", "deploying", False),
            (False, False, "ipmitool-socat", "error", False),
            (False, False, "ipmitool-socat", "clean wait", False),
        ],
    )
    def test_filters(
        self,
        console_enabled,
        maintenance,
        console_interface,
        provision_state,
        should_yield,
    ):
        conn = mock.MagicMock()
        node = Node(
            id="fake-id",
            name="fake-name",
            console_enabled=console_enabled,
            maintenance=maintenance,
            console_interface=console_interface,
            provision_state=provision_state,
        )
        conn.baremetal.nodes.return_value = [node]

        result = list(serial_console_enabler.find_nodes_missing_console(conn))

        if should_yield:
            assert result == [node]
        else:
            assert result == []


def test_enable_console_calls_baremetal_proxy():
    """enable_console must invoke a method that actually exists on the Proxy."""
    conn = mock.MagicMock()
    conn.baremetal = mock.MagicMock(spec=BaremetalProxy)
    node = Node(id="fake-id", name="fake-name")

    serial_console_enabler.enable_console(conn, node)

    conn.baremetal.enable_node_console.assert_called_once_with(node)
