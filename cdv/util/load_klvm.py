from __future__ import annotations

import importlib
import inspect
import os
import pathlib

import pkg_resources
from chik.types.blockchain_format.program import Program
from chik.types.blockchain_format.serialized_program import SerializedProgram
from klvm_tools.klvmc import compile_klvm as compile_klvm_py

compile_klvm = compile_klvm_py


# Handle optional use of klvm_tools_rs if available and requested
if "KLVM_TOOLS_RS" in os.environ:
    try:

        def sha256file(f):
            import hashlib

            m = hashlib.sha256()
            m.update(open(f).read().encode("utf8"))
            return m.hexdigest()

        from klvm_tools_rs import compile_klvm as compile_klvm_rs

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

        def rust_compile_klvm(full_path, output, search_paths=[]):
            treated_include_paths = list(map(translate_path, search_paths))
            print("compile_klvm_rs", full_path, output, treated_include_paths)
            compile_klvm_rs(str(full_path), str(output), treated_include_paths)

            if os.environ["KLVM_TOOLS_RS"] == "check":
                assert False
                orig = str(output) + ".orig"
                compile_klvm_py(full_path, orig, search_paths=search_paths)
                orig256 = sha256file(orig)
                rs256 = sha256file(output)

                if orig256 != rs256:
                    print("Compiled %s: %s vs %s\n" % (full_path, orig256, rs256))
                    print("Aborting compilation due to mismatch with rust")
                    assert orig256 == rs256

        compile_klvm = rust_compile_klvm
    finally:
        pass


def load_serialized_klvm(klvm_filename, package_or_requirement=__name__, search_paths=[]) -> SerializedProgram:
    """
    This function takes a .klvm file in the given package and compiles it to a
    .klvm.hex file if the .hex file is missing or older than the .klvm file, then
    returns the contents of the .hex file as a `Program`.

    klvm_filename: file name
    package_or_requirement: usually `__name__` if the klvm file is in the same package
    """

    hex_filename = f"{klvm_filename}.hex"

    try:
        if pkg_resources.resource_exists(package_or_requirement, klvm_filename):
            full_path = pathlib.Path(pkg_resources.resource_filename(package_or_requirement, klvm_filename))
            output = full_path.parent / hex_filename
            compile_klvm(
                full_path,
                output,
                search_paths=[full_path.parent, pathlib.Path.cwd().joinpath("include"), *search_paths],
            )
    except NotImplementedError:
        # pyinstaller doesn't support `pkg_resources.resource_exists`
        # so we just fall through to loading the hex klvm
        pass

    klvm_hex = pkg_resources.resource_string(package_or_requirement, hex_filename).decode("utf8")
    klvm_blob = bytes.fromhex(klvm_hex)
    return SerializedProgram.from_bytes(klvm_blob)


def load_klvm(klvm_filename, package_or_requirement=__name__, search_paths=[]) -> Program:
    return Program.from_bytes(
        bytes(
            load_serialized_klvm(
                klvm_filename, package_or_requirement=package_or_requirement, search_paths=search_paths
            )
        )
    )
