import builtins
import logging
import yaml
import pytest
from unittest import mock


from hammers import set_image_property


class DummyCompute:
    def __init__(self):
        self.set_image_metadata = mock.MagicMock()


class DummyConn:
    def __init__(self):
        self.compute = DummyCompute()


def test_parse_args_minimal_required():
    args = ["--site-yaml", "site.yaml",
            "--metadata-field", "chameleon-supported",
            "--single-value", "uuid1:yes"]
    parsed = set_image_property.parse_args(args)
    assert parsed.site_yaml == "site.yaml"
    assert parsed.metadata_field == "chameleon-supported"
    assert parsed.single_value == "uuid1:yes"
    assert parsed.values_file is None
    assert parsed.dry_run is False
    assert parsed.debug is False


def test_parse_args_values_file_and_flags():
    args = ["--site-yaml", "site.yaml",
            "--metadata-field", "chameleon-supported",
            "--values-file", "vals.txt",
            "--dry-run", "--debug"]
    parsed = set_image_property.parse_args(args)
    assert parsed.values_file == "vals.txt"
    assert parsed.single_value is None
    assert parsed.dry_run is True
    assert parsed.debug is True


def test_parse_args_mutually_exclusive_args_error():
    with pytest.raises(SystemExit):
        # missing both --single-value and --values-file
        set_image_property.parse_args([
            "--site-yaml", "site.yaml",
            "--metadata-field", "chameleon-supported"
        ])


def test_load_values_from_file(tmp_path):
    contents = "\n".join([
        "# a comment",
        "",
        "uuid1:val1",
        "badline",
    ])
    f = tmp_path / "values.txt"
    f.write_text(contents)

    vals = set_image_property.load_values_from_file(str(f))
    assert vals == [
        ("uuid1", "val1"),
    ]


def test_get_values_from_file(tmp_path):
    f = tmp_path / "vals.txt"
    f.write_text("uuid123:deprecated")
    args = mock.Mock(values_file=str(f), single_value=None)
    result = set_image_property.get_values(args)
    assert result == [("uuid123", "deprecated")]


def test_get_values_file_not_found(monkeypatch, caplog):
    args = mock.Mock(values_file="does_not_exist.txt", single_value=None)
    monkeypatch.setattr(set_image_property, "load_values_from_file",
                        lambda path: (_ for _ in ()).throw(FileNotFoundError))
    with pytest.raises(SystemExit) as ex:
        set_image_property.get_values(args)
    assert ex.value.code == 1
    assert "File not found: does_not_exist.txt" in caplog.text


def test_get_values_single_missing(caplog):
    args = mock.Mock(values_file=None, single_value=None)
    with pytest.raises(SystemExit) as ex:
        set_image_property.get_values(args)
    assert ex.value.code == 1
    assert "--single-value is required" in caplog.text


def test_get_values_single_invalid_format(caplog):
    args = mock.Mock(values_file=None, single_value="badformat")
    with pytest.raises(SystemExit) as ex:
        set_image_property.get_values(args)
    assert ex.value.code == 1
    assert "Invalid format for --single-value" in caplog.text


def test_get_values_single_valid():
    args = mock.Mock(values_file=None, single_value="uuid1:no")
    result = set_image_property.get_values(args)
    assert result == [("uuid1", "no")]


def test_tag_image_dry_run(caplog):
    conn = DummyConn()
    caplog.set_level(logging.DEBUG)
    set_image_property.tag_image(
        conn,
        "uuid1",
        "no",
        "chameleon-supported",
        dry_run=True
    )
    assert "DRY-RUN: would set 'chameleon-supported=no' on image uuid1" in caplog.text
    assert conn.compute.set_image_metadata.call_count == 0


def test_tag_image_success(caplog):
    conn = DummyConn()
    caplog.set_level(logging.DEBUG)
    set_image_property.tag_image(
        conn,
        "uuid2",
        "yes",
        "chameleon-supported",
        dry_run=False
    )
    conn.compute.set_image_metadata.assert_called_once_with(
        "uuid2",
        **{"chameleon-supported": "yes"}
    )
    assert "chameleon-supported=yes successfully set on image uuid2" in caplog.text


def test_tag_image_failure(caplog):
    conn = DummyConn()
    conn.compute.set_image_metadata.side_effect = Exception("failure")
    set_image_property.tag_image(
        conn,
        "uuid3",
        "no",
        "chameleon-supported",
        dry_run=False
    )
    assert "Error tagging image uuid3: failure" in caplog.text


def test_main_site_yaml_load_failure(monkeypatch, caplog):
    args = mock.Mock(
        site_yaml="noexist.yaml",
        debug=False,
        dry_run=False,
        metadata_field="chameleon-supported",
        values_file=None,
        single_value=None
    )
    monkeypatch.setattr(set_image_property, "parse_args", lambda _: args)
    monkeypatch.setattr(builtins, "open",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("failed")))
    with pytest.raises(SystemExit) as ex:
        set_image_property.main([])
    assert ex.value.code == 1
    assert "Failed to load site YAML 'noexist.yaml': failed" in caplog.text


def test_main_missing_cloud(monkeypatch, tmp_path, caplog):
    site = tmp_path / "site.yaml"
    site.write_text(yaml.safe_dump({}))
    args = mock.Mock(
        site_yaml=str(site),
        debug=False,
        dry_run=False,
        metadata_field="chameleon-supported",
        values_file=None,
        single_value=None
    )
    monkeypatch.setattr(set_image_property, "parse_args", lambda _: args)
    with pytest.raises(SystemExit) as ex:
        set_image_property.main([])
    assert ex.value.code == 1
    assert "Required 'image_store_cloud' key not found in site YAML" in caplog.text
