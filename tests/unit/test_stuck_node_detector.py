import os
import sys
from datetime import datetime, timezone
from unittest import mock

import pytest
from openstack import exceptions

from hammers import stuck_node_detector as snd

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
UPDATED = "2026-10-13T11:00:00+00:00"  # one hour before NOW


def make_node(uuid="n1", **kwargs):
    node = {
        "uuid": uuid,
        "name": f"node-{uuid}",
        "provision_state": "available",
        "target_provision_state": None,
        "maintenance": False,
        "maintenance_reason": None,
        "instance_uuid": None,
        "provision_updated_at": UPDATED,
        "reservation": None,
        "last_error": None,
    }
    node.update(kwargs)
    return node


def http_response(body=None, status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = body or {}
    return resp


def make_conn(nodes=(), nova=None, deleted=(), admin_error=None):
    """Strict fake connection: only the read calls we expect exist.

    nova maps instance uuid to "ok", "404" or "500". deleted lists the uuids
    that Nova still has a deleted record for.
    """
    nova = nova or {}

    def get_server(uuid):
        result = nova.get(uuid, "ok")
        if result == "404":
            raise exceptions.NotFoundException("gone")
        if result == "500":
            raise exceptions.HttpException("boom", http_status=500)
        return mock.Mock(id=uuid)

    def compute_get(url, params=None, **kwargs):
        assert url == "/servers"
        assert params["deleted"] == "true"
        assert params["all_tenants"] == "true"
        found = [{"id": u} for u in deleted if u == params["uuid"]]
        return http_response({"servers": found})

    conn = mock.Mock(spec=["baremetal", "compute", "network", "reservation"])
    conn.baremetal = mock.Mock(spec=["get"])
    conn.baremetal.get.return_value = http_response({"nodes": list(nodes)})
    conn.compute = mock.Mock(spec=["get", "get_server", "servers"])
    conn.compute.get.side_effect = compute_get
    conn.compute.get_server.side_effect = get_server
    conn.compute.servers.side_effect = (
        admin_error if admin_error else (lambda **kw: iter([mock.Mock()]))
    )
    conn.network = mock.Mock(spec=[])
    conn.reservation = mock.Mock(spec=[])
    return conn


def run(conn, fraction=0.2):
    nodes = snd.list_nodes(conn)
    return nodes, *snd.find_issues(conn, nodes, NOW, fraction)


def cases(issues):
    return {i["uuid"]: i["case"] for i in issues}


@pytest.mark.parametrize(
    "state", sorted(snd.FAILED_STATES)
)
def test_failed_state(state):
    _, issues, _, ok = run(make_conn([make_node(provision_state=state)]))
    assert ok
    assert cases(issues) == {"n1": "failed_state"}
    assert issues[0]["age"] == 3600


def test_failed_state_in_maintenance_is_not_flagged():
    node = make_node(
        provision_state="deploy failed", maintenance=True, maintenance_reason="RMA"
    )
    _, issues, _, _ = run(make_conn([node]))
    assert issues == []


@pytest.mark.parametrize("state", sorted(snd.TRANSITION_STATES))
def test_stuck_transition(state):
    _, issues, _, _ = run(make_conn([make_node(provision_state=state)]))
    assert cases(issues) == {"n1": "stuck_transition"}


def test_stuck_transition_in_maintenance_is_not_flagged():
    node = make_node(
        provision_state="deploying", maintenance=True, maintenance_reason="x"
    )
    _, issues, _, _ = run(make_conn([node]))
    assert issues == []


@pytest.mark.parametrize("reason", [None, "", "   \n"])
def test_parked_no_reason(reason):
    node = make_node(maintenance=True, maintenance_reason=reason)
    _, issues, _, _ = run(make_conn([node]))
    assert cases(issues) == {"n1": "parked_no_reason"}


def test_parked_with_reason_is_fine():
    node = make_node(maintenance=True, maintenance_reason="ticket 12: bad DIMM")
    _, issues, _, _ = run(make_conn([node]))
    assert issues == []


def test_available_with_instance():
    node = make_node(instance_uuid="i1")
    _, issues, _, _ = run(make_conn([node]))
    assert cases(issues) == {"n1": "available_with_instance"}


def test_healthy_active_node_is_not_flagged():
    node = make_node(provision_state="active", instance_uuid="i1")
    _, issues, failures, ok = run(make_conn([node]))
    assert issues == []
    assert failures == 0
    assert ok


def test_404_with_deleted_record_is_deleted_instance():
    node = make_node(provision_state="deploy failed", instance_uuid="i1")
    conn = make_conn([node], nova={"i1": "404"}, deleted=["i1"])
    _, issues, _, _ = run(conn)
    # takes precedence over failed_state
    assert cases(issues) == {"n1": "deleted_instance"}


def test_deleted_instance_applies_to_active_node():
    node = make_node(provision_state="active", instance_uuid="i1")
    conn = make_conn([node], nova={"i1": "404"}, deleted=["i1"])
    _, issues, _, _ = run(conn)
    assert cases(issues) == {"n1": "deleted_instance"}


def test_404_without_deleted_record_is_unproven_not_deleted():
    node = make_node(provision_state="active", instance_uuid="i1")
    conn = make_conn([node], nova={"i1": "404"}, deleted=["someone-else"])
    _, issues, _, ok = run(conn)
    assert cases(issues) == {"n1": "instance_missing_unproven"}
    assert ok


def test_deleted_lookup_that_ignores_uuid_filter_is_checked_by_id():
    node = make_node(provision_state="active", instance_uuid="i1")
    conn = make_conn([node], nova={"i1": "404"})
    conn.compute.get.side_effect = None
    conn.compute.get.return_value = http_response({"servers": [{"id": "other"}]})
    _, issues, _, _ = run(conn)
    assert cases(issues) == {"n1": "instance_missing_unproven"}


def test_500_is_unknown_and_counted():
    nodes = [
        make_node("n1", provision_state="active", instance_uuid="i1"),
        make_node("n2", provision_state="active", instance_uuid="i2"),
        make_node("n3", provision_state="active", instance_uuid="i3"),
        make_node("n4", provision_state="active", instance_uuid="i4"),
        make_node("n5", provision_state="active", instance_uuid="i5"),
        make_node("n6", provision_state="active", instance_uuid="i6"),
    ]
    conn = make_conn(nodes, nova={"i1": "500"})
    _, issues, failures, ok = run(conn)
    assert issues == []  # unknown is never reported as gone
    assert failures == 1
    assert ok  # 1/6 is under the 0.2 cutoff
    conn.compute.get.assert_not_called()


def test_unknown_node_still_gets_its_state_case():
    node = make_node(provision_state="deploy failed", instance_uuid="i1")
    _, issues, failures, _ = run(make_conn([node], nova={"i1": "500"}))
    assert cases(issues) == {"n1": "failed_state"}
    assert failures == 1


def test_deleted_list_error_is_unknown_not_unproven():
    node = make_node(provision_state="active", instance_uuid="i1")
    conn = make_conn([node], nova={"i1": "404"})
    conn.compute.get.side_effect = exceptions.HttpException("boom", http_status=503)
    _, issues, failures, _ = run(conn)
    assert issues == []
    assert failures == 1


def test_failure_fraction_cutoff():
    nodes = [
        make_node(f"n{i}", provision_state="active", instance_uuid=f"i{i}")
        for i in range(5)
    ]
    nova = {"i0": "500"}  # 1 of 5 = 0.2, not more than 0.2
    assert run(make_conn(nodes, nova=nova))[3] is True

    nova = {"i0": "500", "i1": "500"}  # 2 of 5 = 0.4
    _, _, failures, ok = run(make_conn(nodes, nova=nova))
    assert failures == 2
    assert ok is False

    # the cutoff is configurable
    assert run(make_conn(nodes, nova=nova), fraction=0.5)[3] is True


def test_admin_visibility_failure_fails_run_without_lookups():
    node = make_node(provision_state="active", instance_uuid="i1")
    conn = make_conn([node], admin_error=exceptions.ForbiddenException("no"))
    _, issues, failures, ok = run(conn)
    assert ok is False
    conn.compute.get_server.assert_not_called()
    assert failures == 0
    assert issues == []


def test_age_is_zero_when_unknown_and_never_negative():
    assert snd.issue_age_seconds(make_node(provision_updated_at=None), NOW) == 0
    assert snd.issue_age_seconds(make_node(provision_updated_at="junk"), NOW) == 0
    future = make_node(provision_updated_at="2026-10-14T00:00:00+00:00")
    assert snd.issue_age_seconds(future, NOW) == 0
    naive = make_node(provision_updated_at="2026-10-13T11:00:00")
    assert snd.issue_age_seconds(naive, NOW) == 3600


def test_escape_label():
    assert snd.escape_label('a"b') == 'a\\"b'
    assert snd.escape_label("a\\b") == "a\\\\b"
    assert snd.escape_label("a\nb") == "a\\nb"
    line = snd.sample("m", {"node": 'we"ird\\\nname'}, 3)
    assert line == 'm{node="we\\"ird\\\\\\nname"} 3'


def test_render_metrics():
    nodes = [
        make_node("n1", provision_state="active"),
        make_node("n2", provision_state="deploy failed"),
        make_node("n3", provision_state="deploy failed", maintenance=True),
    ]
    issues = [
        {
            "node": "node-n2",
            "uuid": "n2",
            "provision_state": "deploy failed",
            "case": "failed_state",
            "age": 3600.4,
        }
    ]
    text = snd.render_metrics(nodes, issues, 2, 1760356800.0)
    assert "# TYPE chi_node_census gauge" in text
    assert 'chi_node_census{provision_state="active",maintenance="false"} 1' in text
    assert (
        'chi_node_census{provision_state="deploy failed",maintenance="true"} 1' in text
    )
    assert (
        'chi_node_issue_age_seconds{node="node-n2",uuid="n2",'
        'provision_state="deploy failed",case="failed_state"} 3600' in text
    )
    assert "chi_detector_last_success_timestamp_seconds 1760356800\n" in text
    assert "chi_detector_lookup_failures 2\n" in text
    for name in (
        "chi_node_census",
        "chi_node_issue_age_seconds",
        "chi_detector_last_success_timestamp_seconds",
        "chi_detector_lookup_failures",
    ):
        assert f"# HELP {name} " in text
        assert f"# TYPE {name} gauge" in text
    assert text.endswith("\n")


def test_render_metrics_omits_last_success_when_none():
    text = snd.render_metrics([], [], 0, None)
    assert "last_success" not in text


def test_write_textfile_is_atomic(tmp_path):
    path = tmp_path / "stuck.prom"
    path.write_text("old\n")

    with mock.patch("os.replace", wraps=os.replace) as replace:
        snd.write_textfile(str(path), "new\n")

    src, dst = replace.call_args.args
    assert dst == str(path)
    assert os.path.dirname(src) == str(tmp_path)  # same directory, so atomic
    assert path.read_text() == "new\n"
    assert os.listdir(tmp_path) == ["stuck.prom"]  # temp file is gone
    assert (path.stat().st_mode & 0o777) == 0o644


def test_write_textfile_failure_keeps_old_file(tmp_path):
    path = tmp_path / "stuck.prom"
    path.write_text("old\n")

    with mock.patch("os.replace", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            snd.write_textfile(str(path), "new\n")

    assert path.read_text() == "old\n"
    assert os.listdir(tmp_path) == ["stuck.prom"]


def test_previous_success(tmp_path):
    path = tmp_path / "stuck.prom"
    assert snd.previous_success(str(path)) is None
    assert snd.previous_success(None) is None
    path.write_text(snd.render_metrics([], [], 0, 1760000000.0))
    assert snd.previous_success(str(path)) == 1760000000.0


def run_main(monkeypatch, conn, *args):
    monkeypatch.setattr(sys, "argv", ["stuck_node_detector", *args])
    monkeypatch.setattr(snd.openstack, "connect", lambda cloud=None: conn)
    snd.main()


def test_main_writes_textfile_and_sets_last_success(monkeypatch, tmp_path):
    path = tmp_path / "stuck.prom"
    conn = make_conn([make_node(provision_state="error")])
    run_main(monkeypatch, conn, "--textfile", str(path))
    text = path.read_text()
    assert 'case="failed_state"' in text
    assert snd.LAST_SUCCESS_METRIC in text


def test_main_failed_run_keeps_old_last_success(monkeypatch, tmp_path):
    path = tmp_path / "stuck.prom"
    path.write_text(snd.render_metrics([], [], 0, 1760000000.0))
    conn = make_conn([], admin_error=exceptions.ForbiddenException("no"))
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, conn, "--textfile", str(path))
    assert exc.value.code == 1
    assert f"{snd.LAST_SUCCESS_METRIC} 1760000000\n" in path.read_text()


def test_main_failed_run_without_old_file_has_no_last_success(monkeypatch, tmp_path):
    path = tmp_path / "stuck.prom"
    conn = make_conn([], admin_error=exceptions.ForbiddenException("no"))
    with pytest.raises(SystemExit):
        run_main(monkeypatch, conn, "--textfile", str(path))
    assert snd.LAST_SUCCESS_METRIC not in path.read_text()


def test_main_prints_to_stdout_without_textfile(monkeypatch, capsys):
    run_main(monkeypatch, make_conn([make_node(provision_state="error")]))
    assert "chi_node_issue_age_seconds" in capsys.readouterr().out


def test_detector_is_read_only(monkeypatch, tmp_path):
    """Run end to end against a strict fake and check only reads were made."""
    nodes = [
        make_node("n1", provision_state="deploy failed", instance_uuid="i1"),
        make_node("n2", provision_state="active", instance_uuid="i2"),
        make_node("n3", provision_state="deploying"),
        make_node("n4", maintenance=True),
        make_node("n5", provision_state="active", instance_uuid="i5"),
    ]
    conn = make_conn(nodes, nova={"i1": "404", "i2": "500", "i5": "404"}, deleted=["i1"])
    # 1 of 3 lookups fails, over the default 0.2 cutoff, so the run exits 1
    with pytest.raises(SystemExit):
        run_main(monkeypatch, conn, "--textfile", str(tmp_path / "x.prom"))

    # drop calls on returned objects, e.g. "baremetal.get().json"
    called = {c[0].split("()")[0] for c in conn.mock_calls}
    assert called <= {
        "baremetal.get",
        "compute.get",
        "compute.get_server",
        "compute.servers",
    }
    mutating = (
        "set_node_provision_state",
        "set_node_maintenance",
        "unset_node_maintenance",
        "delete_server",
        "update_",
        "delete_",
        "create_",
        "post",
        "put",
        "patch",
        "delete",
    )
    assert not [c for c in called if c.rsplit(".", 1)[-1].startswith(mutating)]
    # and the strict spec means a mutating call could not even be made
    with pytest.raises(AttributeError):
        conn.baremetal.set_node_provision_state("n1", "deleted")
    with pytest.raises(AttributeError):
        conn.compute.delete_server("i1")
    # every ironic and nova call was a GET
    assert conn.baremetal.get.call_count == 1
