# pyright: reportInvalidTypeVarUse=information

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable, Literal, Self, TypeVar, overload

from pydantic import SkipValidation
from pydantic.dataclasses import dataclass

from hexdoc.model import DEFAULT_CONFIG, HexdocModel, ValidationContext
from hexdoc.plugin import PluginManager
from hexdoc.utils.deserialize.json import JSONDict, decode_json_dict
from hexdoc.utils.iterators import must_yield_something
from hexdoc.utils.path import strip_suffixes, write_to_path

from .properties import Properties
from .resource import ResourceLocation, ResourceType
from .resource_dir import PathResourceDir

METADATA_SUFFIX = ".hexdoc.json"

_T = TypeVar("_T")
_T_Model = TypeVar("_T_Model", bound=HexdocModel)

ExportFn = Callable[[_T, _T | None], str]


@dataclass(config=DEFAULT_CONFIG | {"arbitrary_types_allowed": True}, kw_only=True)
class ModResourceLoader:
    props: Properties
    book_id: ResourceLocation | None
    export_dir: Path | None
    resource_dirs: list[PathResourceDir]
    _stack: SkipValidation[ExitStack]

    @classmethod
    def clean_and_load_all(
        cls,
        props: Properties,
        pm: PluginManager,
        *,
        export: bool = True,
    ):
        # clear the export dir so we start with a clean slate
        if props.export_dir and export:
            subprocess.run(["git", "clean", "-fdX", props.export_dir])

            write_to_path(
                props.export_dir / "__init__.py",
                dedent(
                    """\
                    # This directory is auto-generated by hexdoc.
                    # Do not edit or commit these files.
                    """
                ),
            )

        return cls.load_all(props, pm, export=export)

    @classmethod
    def load_all(
        cls,
        props: Properties,
        pm: PluginManager,
        *,
        export: bool = True,
    ) -> Self:
        export_dir = props.export_dir if export else None
        stack = ExitStack()
        return cls(
            props=props,
            book_id=props.book,
            export_dir=export_dir,
            resource_dirs=[
                path_resource_dir
                for resource_dir in props.resource_dirs
                for path_resource_dir in stack.enter_context(resource_dir.load(pm))
            ],
            _stack=stack,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc_details: Any):
        return self._stack.__exit__(*exc_details)

    def close(self):
        self._stack.close()

    def _map_own_assets(self, folder: str, *, root: str | Path):
        return {
            id: path.resolve().relative_to(root)
            for _, id, path in self.find_resources(
                "assets",
                namespace=self.props.modid,
                folder="",
                glob=f"{folder}/**/*.*",
                allow_missing=True,
            )
        }

    def load_metadata(
        self,
        *,
        name_pattern: str = "{modid}",
        model_type: type[_T_Model],
        allow_missing: bool = False,
    ) -> dict[str, _T_Model]:
        """eg. `"{modid}.patterns"`"""
        metadata = dict[str, _T_Model]()

        for resource_dir in self.resource_dirs:
            # skip if we've already loaded this mod's metadata
            modid = resource_dir.modid
            if modid is None or modid in metadata:
                continue

            try:
                _, metadata[modid] = self.load_resource(
                    Path(name_pattern.format(modid=modid) + METADATA_SUFFIX),
                    decode=model_type.model_validate_json,
                    export=False,
                )
            except FileNotFoundError:
                if allow_missing:
                    continue
                else:
                    raise

        return metadata

    # TODO: maybe this should take lang as a variable?
    @must_yield_something
    def load_book_assets(
        self,
        book_id: ResourceLocation,
        folder: Literal["categories", "entries", "templates"],
        use_resource_pack: bool,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, JSONDict]]:
        is_extension = book_id != self.book_id
        if is_extension:
            yield from self._load_book_assets(
                book_id,
                folder,
                use_resource_pack=use_resource_pack,
                allow_missing=True,
            )

        if self.book_id:
            yield from self._load_book_assets(
                self.book_id,
                folder,
                use_resource_pack=use_resource_pack,
                allow_missing=is_extension,
            )

    def _load_book_assets(
        self,
        book_id: ResourceLocation,
        folder: Literal["categories", "entries", "templates"],
        *,
        use_resource_pack: bool,
        allow_missing: bool,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, JSONDict]]:
        yield from self.load_resources(
            type="assets" if use_resource_pack else "data",
            folder=Path("patchouli_books")
            / book_id.path
            / self.props.default_lang
            / folder,
            namespace=book_id.namespace,
            allow_missing=allow_missing,
        )

    @overload
    def load_resource(
        self,
        type: ResourceType,
        folder: str | Path,
        id: ResourceLocation,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
    ) -> tuple[PathResourceDir, _T]:
        ...

    @overload
    def load_resource(
        self,
        path: Path,
        /,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
    ) -> tuple[PathResourceDir, _T]:
        ...

    def load_resource(
        self,
        *args: Any,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
        **kwargs: Any,
    ) -> tuple[PathResourceDir, _T]:
        """Find the first file with this resource location in `resource_dirs`.

        If no file extension is provided, `.json` is assumed.

        Raises FileNotFoundError if the file does not exist.
        """

        resource_dir, path = self.find_resource(*args, **kwargs)
        return resource_dir, self._load_path(
            resource_dir,
            path,
            decode=decode,
            export=export,
        )

    @overload
    def find_resource(
        self,
        type: ResourceType,
        folder: str | Path,
        id: ResourceLocation,
    ) -> tuple[PathResourceDir, Path]:
        ...

    @overload
    def find_resource(
        self,
        path: Path,
        /,
    ) -> tuple[PathResourceDir, Path]:
        ...

    def find_resource(
        self,
        type: ResourceType | Path,
        folder: str | Path | None = None,
        id: ResourceLocation | None = None,
    ) -> tuple[PathResourceDir, Path]:
        """Find the first file with this resource location in `resource_dirs`.

        If no file extension is provided, `.json` is assumed.

        Raises FileNotFoundError if the file does not exist.
        """

        if isinstance(type, Path):
            path_stub = type
        else:
            assert folder is not None and id is not None
            path_stub = id.file_path_stub(type, folder)

        # check by descending priority, return the first that exists
        for resource_dir in self.resource_dirs:
            path = resource_dir.path / path_stub
            if path.is_file():
                return resource_dir, path

        raise FileNotFoundError(f"Path {path_stub} not found in any resource dir")

    @overload
    def load_resources(
        self,
        type: ResourceType,
        *,
        namespace: str,
        folder: str | Path,
        glob: str | list[str] = "**/*",
        allow_missing: bool = False,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, _T]]:
        ...

    @overload
    def load_resources(
        self,
        type: ResourceType,
        *,
        folder: str | Path,
        id: ResourceLocation,
        allow_missing: bool = False,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, _T]]:
        ...

    def load_resources(
        self,
        type: ResourceType,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
        **kwargs: Any,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, _T]]:
        """Like `find_resources`, but also loads the file contents and reexports it."""
        for resource_dir, value_id, path in self.find_resources(type, **kwargs):
            value = self._load_path(
                resource_dir,
                path,
                decode=decode,
                export=export,
            )
            yield resource_dir, value_id, value

    @overload
    def find_resources(
        self,
        type: ResourceType,
        *,
        namespace: str,
        folder: str | Path,
        glob: str | list[str] = "**/*",
        allow_missing: bool = False,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, Path]]:
        ...

    @overload
    def find_resources(
        self,
        type: ResourceType,
        *,
        folder: str | Path,
        id: ResourceLocation,
        allow_missing: bool = False,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, Path]]:
        ...

    def find_resources(
        self,
        type: ResourceType,
        *,
        folder: str | Path,
        id: ResourceLocation | None = None,
        namespace: str | None = None,
        glob: str | list[str] = "**/*",
        allow_missing: bool = False,
    ) -> Iterator[tuple[PathResourceDir, ResourceLocation, Path]]:
        """Search for a glob under a given resource location in all of `resource_dirs`.

        Files are returned from lowest to highest priority in the load order, ie. later
        files should overwrite earlier ones.

        If no file extension is provided for glob, `.json` is assumed.

        Raises FileNotFoundError if no files were found in any resource dir.

        For example:
        ```py
        props.find_resources(
            "assets",
            "lang/subdir",
            namespace="*",
            glob="*.flatten.json5",
        )

        # [(hexcasting:en_us, .../resources/assets/hexcasting/lang/subdir/en_us.json)]
        ```
        """

        if id is not None:
            namespace = id.namespace
            glob = id.path

        # eg. assets/*/lang/subdir
        if namespace is not None:
            base_path_stub = Path(type) / namespace / folder
        else:
            raise RuntimeError(
                "No overload matches the specified arguments (expected id or namespace)"
            )

        # glob for json files if not provided
        globs = [glob] if isinstance(glob, str) else glob
        for i in range(len(globs)):
            if not Path(globs[i]).suffix:
                globs[i] += ".json"

        # find all files matching the resloc
        found_any = False
        for resource_dir in reversed(self.resource_dirs):
            # eg. .../resources/assets/*/lang/subdir
            for base_path in resource_dir.path.glob(base_path_stub.as_posix()):
                for glob_ in globs:
                    # eg. .../resources/assets/hexcasting/lang/subdir/*.flatten.json5
                    for path in base_path.glob(glob_):
                        # only strip json/json5, not eg. png
                        id_path = path.relative_to(base_path)
                        if "json" in path.name:
                            id_path = strip_suffixes(id_path)

                        id = ResourceLocation(
                            # eg. ["assets", "hexcasting", "lang", ...][1]
                            namespace=path.relative_to(resource_dir.path).parts[1],
                            path=id_path.as_posix(),
                        )

                        if path.is_file():
                            found_any = True
                            yield resource_dir, id, path

        # if we never yielded any files, raise an error
        if not allow_missing and not found_any:
            raise FileNotFoundError(
                f"No files found under {base_path_stub / repr(globs)} in any resource dir"
            )

    def _load_path(
        self,
        resource_dir: PathResourceDir,
        path: Path,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | Literal[False] | None = None,
    ) -> _T:
        if not path.is_file():
            raise FileNotFoundError(path)

        logging.getLogger(__name__).info(f"Loading {path}")

        data = path.read_text("utf-8")
        value = decode(data)

        if resource_dir.reexport and export is not False:
            self.export(
                path.relative_to(resource_dir.path),
                data,
                value,
                decode=decode,
                export=export,
            )

        return value

    @overload
    def export(self, /, path: Path, data: str) -> None:
        ...

    @overload
    def export(
        self,
        /,
        path: Path,
        data: str,
        value: _T,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | None = None,
    ) -> None:
        ...

    def export(
        self,
        path: Path,
        data: str,
        value: _T = None,
        *,
        decode: Callable[[str], _T] = decode_json_dict,
        export: ExportFn[_T] | None = None,
    ) -> None:
        if not self.export_dir:
            return
        out_path = self.export_dir / path

        logging.getLogger(__name__).debug(f"Exporting {path} to {out_path}")
        if export is None:
            out_data = data
        else:
            try:
                old_value = decode(out_path.read_text("utf-8"))
            except FileNotFoundError:
                old_value = None

            out_data = export(value, old_value)

        write_to_path(out_path, out_data)


class LoaderContext(ValidationContext):
    loader: ModResourceLoader

    @property
    def props(self):
        return self.loader.props
