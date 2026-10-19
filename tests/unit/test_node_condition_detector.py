from unittest import mock

import pytest

from openstack import exceptions
from openstack.baremetal.v1.node import Node
from openstack.compute.v2.server import Server
from prometheus_client import CollectorRegistry

from hammers import node_condition_detector
from hammers.node_condition_detector import Condition, NodeConditionCollector


def fake_conn(get_server):
    """Three nodes: one whose server nova lists, one whose server nova doesn't
    list (srv-gone), and one with no instance. get_server is what nova's GET
    of srv-gone does: raise, or return a server."""
    conn = mock.MagicMock()
    conn.baremetal.nodes.return_value = [
        Node(id="n-healthy", name="healthy", provision_state="active", instance_id="srv-healthy"),
        Node(id="n-orphan", name="orphan", provision_state="active", instance_id="srv-gone"),
        Node(id="n-free", name="free", provision_state="available", instance_id=None),
    ]
    conn.compute.servers.return_value = [Server(id="srv-healthy")]
    conn.compute.get_server.side_effect = get_server
    return conn


def scrape(collector):
    registry = CollectorRegistry()
    registry.register(collector)
    return registry


def condition_labels(check, c):
    return {"check": check, **c._asdict()}


def test_flags_node_whose_server_nova_returns_404_for():
    conn = fake_conn(get_server=exceptions.NotFoundException())

    orphans = node_condition_detector.find_orphans(conn)

    assert [n.id for n in orphans] == ["n-orphan"]


def test_does_not_flag_server_created_after_the_list():
    # srv-gone isn't in nova's list, but the GET finds it: it was created after
    # the list was taken, so the node is fine
    conn = fake_conn(get_server=lambda server_id: Server(id=server_id))

    assert node_condition_detector.find_orphans(conn) == []


def test_serves_each_checks_conditions_and_success_time():
    collector = NodeConditionCollector(node_condition_detector.CHECKS)

    with mock.patch("time.time", return_value=100):
        collector.run(fake_conn(get_server=exceptions.NotFoundException()))

    registry = scrape(collector)
    orphan = Condition("orphaned_instance", "n-orphan", "orphan", "active", instance_uuid="srv-gone")
    assert registry.get_sample_value(
        "chameleon_node_condition", condition_labels("orphaned_instance", orphan)
    ) == 1
    assert registry.get_sample_value(
        "chameleon_node_check_last_success_timestamp_seconds", {"check": "orphaned_instance"}
    ) == 100


def test_failing_check_keeps_its_last_results_and_does_not_affect_others():
    a = Condition("a", "n-1", "one", "active")
    b = Condition("b", "n-2", "two", "active")
    flaky = mock.Mock(side_effect=[[b], RuntimeError("nova down")])
    collector = NodeConditionCollector({"good": lambda conn: [a], "flaky": flaky})

    with mock.patch("time.time", return_value=100):
        assert collector.run(conn=None)
    with mock.patch("time.time", return_value=200):
        assert not collector.run(conn=None)

    registry = scrape(collector)
    # flaky's results and success time stay from the first run, so its time goes stale
    assert registry.get_sample_value("chameleon_node_condition", condition_labels("flaky", b)) == 1
    assert registry.get_sample_value("chameleon_node_check_last_success_timestamp_seconds", {"check": "flaky"}) == 100
    assert registry.get_sample_value("chameleon_node_check_last_success_timestamp_seconds", {"check": "good"}) == 200


def test_check_that_never_succeeded_reports_last_success_0():
    # so NodeCheckStale fires for it instead of the metric being missing
    collector = NodeConditionCollector({"broken": mock.Mock(side_effect=RuntimeError("no auth"))})

    collector.run(conn=None)

    registry = scrape(collector)
    assert registry.get_sample_value("chameleon_node_check_last_success_timestamp_seconds", {"check": "broken"}) == 0


def test_serves_metrics_only_on_the_given_address(monkeypatch):
    # on a control node, --addr keeps /metrics off the public interface
    monkeypatch.setattr("sys.argv", ["node_condition_detector", "--addr", "10.0.0.5", "--port", "9199"])
    with (
        mock.patch("openstack.connect"),
        mock.patch("hammers.node_condition_detector.start_http_server") as serve,
        mock.patch("time.sleep", side_effect=SystemExit),  # stop after the first run
        pytest.raises(SystemExit),
    ):
        node_condition_detector.main()

    serve.assert_called_once()
    assert serve.call_args.args == (9199,)
    assert serve.call_args.kwargs["addr"] == "10.0.0.5"
