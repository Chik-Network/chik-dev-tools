from __future__ import annotations

import importlib
import inspect
import os
import pathlib

import importlib_resources
from chik.types.blockchain_format.program import Program
from chik.types.blockchain_format.serialized_program import SerializedProgram
from clvk_tools.clvkc import compile_clvk as compile_clvk_py

compile_clvk = compile_clvk_py


# Handle optional use of clvk_tools_rs if available and requested
if "CLVK_TOOLS_RS" in os.environ:
    try:

        def sha256file(f):
            import hashlib

            m = hashlib.sha256()
            m.update(open(f).read().encode("utf8"))
            return m.hexdigest()

        from clvk_tools_rs import compile_clvk as compile_clvk_rs  # type: ignore[import-untyped]

        def translate_path(p_):
            p = str(p_)
            if os.path.isdir(p):
                return p
            else:
                try:
                    module_object = importlib.import_module(p)
                    return os.path.dirname(inspect.getfile(module_object))
                except Exception:
                    return p

        def rust_compile_clvk(full_path, output, search_paths=[]):
            treated_include_paths = list(map(translate_path, search_paths))
            print("compile_clvk_rs", full_path, output, treated_include_paths)
            compile_clvk_rs(str(full_path), str(output), treated_include_paths)

            if os.environ["CLVK_TOOLS_RS"] == "check":
                assert False
                orig = str(output) + ".orig"
                compile_clvk_py(full_path, orig, search_paths=search_paths)
                orig256 = sha256file(orig)
                rs256 = sha256file(output)

                if orig256 != rs256:
                    print(f"Compiled {full_path}: {orig256} vs {rs256}\n")
                    print("Aborting compilation due to mismatch with rust")
                    assert orig256 == rs256

        compile_clvk = rust_compile_clvk
    finally:
        pass


def load_serialized_clvk(clvk_filename, package_or_requirement=__name__, search_paths=[]) -> SerializedProgram:
    """
    This function takes a .clvk file in the given package and compiles it to a
    .clvk.hex file if the .hex file is missing or older than the .clvk file, then
    returns the contents of the .hex file as a `Program`.

    clvk_filename: file name
    package_or_requirement: usually `__name__` if the clvk file is in the same package
    """

    hex_filename = f"{clvk_filename}.hex"

    resources = importlib_resources.files(package_or_requirement)

    try:
        full_path = pathlib.Path(str(resources.joinpath(clvk_filename)))
        output = full_path.parent / hex_filename
        compile_clvk(
            full_path,
            output,
            search_paths=[full_path.parent, pathlib.Path.cwd().joinpath("include"), *search_paths],
        )
    except Exception:
        # so we just fall through to loading the hex clvk
        pass

    clvk_path = resources.joinpath(hex_filename)
    clvk_hex = clvk_path.read_text(encoding="utf-8")
    clvk_blob = bytes.fromhex(clvk_hex)
    return SerializedProgram.from_bytes(clvk_blob)


def load_clvk(clvk_filename, package_or_requirement=__name__, search_paths=[]) -> Program:
    return Program.from_bytes(
        bytes(
            load_serialized_clvk(
                clvk_filename, package_or_requirement=package_or_requirement, search_paths=search_paths
            )
        )
    )
