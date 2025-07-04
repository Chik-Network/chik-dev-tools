from __future__ import annotations

import os
import shutil
from pathlib import Path

import click
import pytest
from chik.util.bech32m import decode_puzzle_hash, encode_puzzle_hash
from chik.util.hash import std_hash
from chik_rs.sized_bytes import bytes32

from cdv import __version__
from cdv.cmds.chik_inspect import inspect_cmd
from cdv.cmds.clsp import clsp_cmd
from cdv.cmds.rpc import rpc_cmd

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def monkey_patch_click() -> None:
    # this hacks around what seems to be an incompatibility between the python from `pyinstaller`
    # and `click`
    #
    # Not 100% sure on the details, but it seems that `click` performs a check on start-up
    # that `codecs.lookup(locale.getpreferredencoding()).name != 'ascii'`, and refuses to start
    # if it's not. The python that comes with `pyinstaller` fails this check.
    #
    # This will probably cause problems with the command-line tools that use parameters that
    # are not strict ascii. The real fix is likely with the `pyinstaller` python.

    import click.core

    click.core._verify_python3_env = lambda *args, **kwargs: 0  # type: ignore


@click.group(
    help="\n  Dev tooling for Chik development \n",
    context_settings=CONTEXT_SETTINGS,
)
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.ensure_object(dict)


@cli.command("test", short_help="Run the local test suite (located in ./tests)")
@click.argument("tests", default="./tests", required=False)
@click.option(
    "-d",
    "--discover",
    is_flag=True,
    help="list the tests without running them",
)
@click.option(
    "-i",
    "--init",
    is_flag=True,
    help="Create the test directory and/or add a new test skeleton",
)
def test_cmd(tests: str, discover: bool, init: str):
    test_paths: list[str] = list(map(str, Path.cwd().glob(tests)))
    if init:
        test_dir = Path(os.getcwd()).joinpath("tests")
        if not test_dir.exists():
            os.mkdir("tests")

        import cdv.test as testlib

        # It finds these files relative to its position in the venv
        # If the cdv/test/__init__.py file or any of the relvant files move, this will break
        src_path = Path(testlib.__file__).parent.joinpath("test_skeleton.py")
        dest_path: Path = test_dir.joinpath("test_skeleton.py")
        shutil.copyfile(src_path, dest_path)
        dest_path_init: Path = test_dir.joinpath("__init__.py")
        open(dest_path_init, "w")
    if discover:
        pytest.main(["--collect-only", *test_paths])
    elif not init:
        pytest.main([*test_paths])


@cli.command("hash", short_help="SHA256 hash UTF-8 strings or bytes (use 0x prefix for bytes)")
@click.argument("data", nargs=1, required=True)
def hash_cmd(data: str):
    if data[:2] == "0x":
        hash_data = bytes.fromhex(data[2:])
    else:
        hash_data = bytes(data, "utf-8")
    print(std_hash(hash_data))


@cli.command("encode", short_help="Encode a puzzle hash to a bech32m address")
@click.argument("puzzle_hash", nargs=1, required=True)
@click.option(
    "-p",
    "--prefix",
    default="xck",
    show_default=True,
    required=False,
    help="The prefix to encode with",
)
def encode_cmd(puzzle_hash: str, prefix: str):
    print(encode_puzzle_hash(bytes32.from_hexstr(puzzle_hash), prefix))


@cli.command("decode", short_help="Decode a bech32m address to a puzzle hash")
@click.argument("address", nargs=1, required=True)
def decode_cmd(address: str):
    print(decode_puzzle_hash(address).hex())


cli.add_command(clsp_cmd)
cli.add_command(inspect_cmd)
cli.add_command(rpc_cmd)


def main() -> None:
    monkey_patch_click()
    cli()  # pylint: disable=no-value-for-parameter


if __name__ == "__main__":
    main()
