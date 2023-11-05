from pathlib import Path
from typing import Any

import pytest
from hexdoc.plugin import PluginManager
from pytest import MonkeyPatch, Parser
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode
from syrupy.types import SerializableData, SerializedData

longrun = pytest.mark.skipif("not config.getoption('longrun')")
nox_only = pytest.mark.skipif("not config.getoption('nox')")


# https://stackoverflow.com/a/43938191
def pytest_addoption(parser: Parser):
    parser.addoption(
        "--no-longrun",
        action="store_false",
        dest="longrun",
        help="disable longrun-decorated tests",
    )
    parser.addoption(
        "--nox",
        action="store_true",
        dest="nox",
        help="enable nox_only-decorated tests",
    )


class FilePathSnapshotExtension(SingleFileSnapshotExtension):
    _write_mode = WriteMode.TEXT

    def serialize(self, data: SerializableData, **_: Any) -> SerializedData:
        match data:
            case str() | Path():
                return self._read_file_at_path(Path(data))
            case _:
                raise TypeError(f"Expected StrPath, got {type(data)}: {data}")

    def _read_file_at_path(self, path: Path):
        if self._write_mode is WriteMode.BINARY:
            return path.read_bytes()
        return path.read_text(self._text_encoding)


@pytest.fixture
def path_snapshot(snapshot: SnapshotAssertion):
    return snapshot.use_extension(FilePathSnapshotExtension)


@pytest.fixture
def pm():
    return PluginManager()


@pytest.fixture(scope="session")
def monkeysession():
    with MonkeyPatch.context() as mp:
        yield mp
