import datetime
import json
import pytest
import requests


from unittest import mock


from hammers import image_deployer


class FakeResponse:
    def __init__(self, status_code=200, text="", content=b"", chunks=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self._chunks = chunks or []

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            yield chunk

    def json(self):
        return json.loads(self.text)


class DummyImage:
    def __init__(self, current_value, name):
        self.name = name
        self.properties = {"current": current_value}


class DummyImageService:
    def __init__(self, images_to_return):
        self._images = images_to_return

    def images(self, name=None, visibility=None):
        filtered = [
            image for image in self._images
            if (name is None or image.name == name)
        ]
        return iter(filtered)


class DummyConn:
    def __init__(self, images_to_return=None):
        if images_to_return is None:
            images_to_return = []
        self.image = DummyImageService(images_to_return)


def test_parse_args_minimal_required():
    args = ["--site-yaml", "mysite.yaml"]
    parsed = image_deployer.parse_args(args)

    assert parsed.site_yaml == "mysite.yaml"
    assert parsed.dry_run is False
    assert parsed.debug is False


def test_parse_args_dry_run_debug():
    args = ["--dry-run", "--debug", "--site-yaml", "mysite.yaml"]
    parsed = image_deployer.parse_args(args)

    assert parsed.site_yaml == "mysite.yaml"
    assert parsed.dry_run is True
    assert parsed.debug is True


def test_get_current_value_success(monkeypatch):
    dummy = {"key": "value"}
    resp = FakeResponse(status_code=200, text=json.dumps(dummy))
    monkeypatch.setattr(requests, "get", lambda url: resp)

    result = image_deployer.get_current_value(
        "http://image/store",
        "chameleon-supported-images",
        "prod"
    )
    assert result == dummy


def test_get_current_value_failure(monkeypatch):
    resp = FakeResponse(status_code=404, content=b"not found")
    monkeypatch.setattr(requests, "get", lambda url: resp)

    with pytest.raises(Exception) as ex:
        image_deployer.get_current_value("a", "b", "c")
    assert "Error getting current value" in str(ex.value)


@pytest.mark.parametrize(
    "image_name, images_to_return, current, expected_result",
    [
        # Not present in site images -> should sync
        ("CC-Ubuntu24.04", [], "v1", True),
        # Present but not current -> should sync
        ("CC-Ubuntu24.04", [DummyImage("old", "CC-Ubuntu24.04")], "new", True),
        # Present and current -> should not sync
        ("CC-Ubuntu24.04", [DummyImage("v1", "CC-Ubuntu24.04")], "v1", False),
        # Multiple images -> should not sync
        ("CC-Ubuntu24.04", [DummyImage("v1", "CC-Ubuntu24.04"),
                            DummyImage("v1", "CC-Ubuntu24.04")], "v1", False),
    ]
)
def test_should_sync_image(image_name, images_to_return, current, expected_result):
    conn = DummyConn(images_to_return)
    site_images = image_deployer.get_site_images(conn)
    assert image_deployer.should_sync_image(
        conn, image_name, site_images, current
    ) is expected_result


def test_download_object_to_file_success(tmp_path, monkeypatch):
    chunks = [b"a", b"b", b"c"]
    fake = FakeResponse(status_code=200, chunks=chunks)
    monkeypatch.setattr(requests, "get", lambda url, stream=True: fake)

    target = tmp_path / "out.bin"
    with open(target, "wb+") as atempfile:
        image_deployer.download_object_to_file(
            "http://notneeded",
            "/does/not/matter",
            "notneeded",
            atempfile
        )

    data = target.read_bytes()
    assert data == b"".join(chunks)


def test_download_object_to_file_failure(monkeypatch, tmp_path):
    fake = FakeResponse(status_code=403, content=b"denied")
    monkeypatch.setattr(requests, "get", lambda url, stream=True: fake)
    with open(tmp_path / "dummy", "wb+") as atempfile:
        with pytest.raises(Exception) as ex:
            image_deployer.download_object_to_file("a", "b", "c", atempfile)
    assert "Error downloading object" in str(ex.value)


def test_get_available_images_success(monkeypatch):
    storage_url = "http://image/store/url"
    base = "chameleon-supported-images"
    scope = "prod"
    current_values = {"CC-Ubuntu22.04": "20250422-v1-amd",
                      "CC-Ubuntu22.04-ARM": "20250422-v1-arm"}
    image_type = "qcow2"

    body = "\n".join([
        "ignored-first-line",
        f"{scope}/versions/20250422-v1-amd/CC-Ubuntu22.04.manifest",
        f"{scope}/versions/20250422-v1-arm/CC-Ubuntu22.04-ARM.manifest",
    ])
    resp = FakeResponse(status_code=200, text=body)
    monkeypatch.setattr(requests, "get", lambda url: resp)

    imgs = image_deployer.get_available_images(
        storage_url,
        base,
        scope,
        current_values,
        image_type
    )
    assert len(imgs) == 2

    img = imgs[0]
    assert isinstance(img, image_deployer.Image)
    assert img.name == "CC-Ubuntu22.04"
    assert img.manifest_name == "CC-Ubuntu22.04.manifest"
    assert img.disk_name == "CC-Ubuntu22.04.qcow2"
    assert img.current_path == "prod/versions/20250422-v1-amd"
    assert img.container_path == "chameleon-supported-images/prod/versions/20250422-v1-amd"

    img = imgs[1]
    assert isinstance(img, image_deployer.Image)
    assert img.name == "CC-Ubuntu22.04-ARM"
    assert img.manifest_name == "CC-Ubuntu22.04-ARM.manifest"
    assert img.disk_name == "CC-Ubuntu22.04-ARM.qcow2"
    assert img.current_path == "prod/versions/20250422-v1-arm"
    assert img.container_path == "chameleon-supported-images/prod/versions/20250422-v1-arm"


def test_get_available_images_failure(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda url: FakeResponse(status_code=500, content=b"bad")
    )
    with pytest.raises(Exception) as ex:
        image_deployer.get_available_images("a", "b", "c", {"X": "1"}, "raw")
    assert "Error getting available images" in str(ex.value)


@pytest.mark.parametrize("status, payload, raises", [
    (200, {"a": 1}, False),
    (500, None, True),
])
def test_get_manifest_data(status, payload, raises, monkeypatch):
    raised_ex = False
    if status == 200:
        text = json.dumps(payload)
        fake = FakeResponse(status_code=200)
        fake.text = text
        monkeypatch.setattr(requests, "get", lambda url: fake)
        result = image_deployer.get_manifest_data("http://goodurl")
        assert result == payload
    elif status == 500:
        fake = FakeResponse(status_code=500, content=b"err")
        monkeypatch.setattr(requests, "get", lambda url: fake)
        with pytest.raises(Exception) as ex:
            raised_ex = True
            image_deployer.get_manifest_data("http://badurl")
        assert "Error downloading object" in str(ex.value)
    else:
        pytest.fail("Unexpected status code run")
    assert raised_ex == raises


@pytest.mark.parametrize("status, payload, raises", [
    ("success", [
        mock.Mock(name="CC-Ubuntu22.04.raw"),
        mock.Mock(name="CC-Ubuntu24.04.raw"),
    ], False),
    ("fail", Exception("Connection error"), True),
])
def test_get_site_images(status, payload, raises):
    conn = mock.Mock()

    if raises:
        conn.image.images.side_effect = payload
    else:
        conn.image.images.return_value = payload

    if raises:
        with pytest.raises(Exception) as ex:
            image_deployer.get_site_images(conn)
        assert "Connection error" in str(ex.value)
    else:
        images = image_deployer.get_site_images(conn)

        conn.image.images.assert_called_once_with(visibility="public")
        assert isinstance(images, list)
        assert len(images) == len(payload)
        for expected, actual in zip(payload, images):
            assert expected.name == actual


@pytest.mark.parametrize("properties, expected_type, raises", [
    ({"build-timestamp": "2024-04-27 15:30:45.123456"}, datetime.datetime, False),
    ({"build-timestamp": "1677628504.897832"}, datetime.datetime, False),
    ({}, str, True),
    ({"build-timestamp": "invalid-timestamp"}, Exception, False),
])
def test_get_image_build_timestamp(properties, expected_type, raises, caplog):
    image = mock.Mock()
    image.properties = properties

    if expected_type is Exception:
        with pytest.raises(Exception, match="Invalid build_timestamp format"):
            image_deployer.get_image_build_timestamp(image)
    else:
        result = image_deployer.get_image_build_timestamp(image)
        if expected_type is str:
            assert isinstance(result, str)
            assert "_" not in result
        else:
            assert isinstance(result, expected_type)

    if raises:
        assert any("Unable to find build-timestamp" in message for message in caplog.text.splitlines())
    else:
        assert "Unable to find build-timestamp" not in caplog.text
