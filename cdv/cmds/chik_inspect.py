from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pprint import pprint
from secrets import token_bytes
from typing import Any, Callable, ClassVar, Optional, Union

import click
from chik._tests.util.get_name_puzzle_conditions import get_name_puzzle_conditions
from chik.consensus.cost_calculator import NPCResult
from chik.consensus.default_constants import DEFAULT_CONSTANTS
from chik.full_node.bundle_tools import simple_solution_generator
from chik.types.blockchain_format.coin import Coin
from chik.types.blockchain_format.program import INFINITE_COST, Program
from chik.types.coin_record import CoinRecord
from chik.types.coin_spend import CoinSpend, make_spend
from chik.types.generator_types import BlockGenerator
from chik.util.byte_types import hexstr_to_bytes
from chik.util.condition_tools import conditions_dict_for_solution, pkm_pairs_for_conditions_dict
from chik.util.config import load_config
from chik.util.default_root import DEFAULT_ROOT_PATH
from chik.util.keychain import bytes_to_mnemonic, mnemonic_to_seed
from chik.wallet.derive_keys import _derive_path
from chik.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import (
    DEFAULT_HIDDEN_PUZZLE_HASH,
    calculate_synthetic_public_key,
    calculate_synthetic_secret_key,
)
from chik.wallet.wallet_spend_bundle import WalletSpendBundle
from chik_rs import AugSchemeMPL, G1Element, G2Element, PrivateKey
from chik_rs.sized_bytes import bytes32
from chik_rs.sized_ints import uint32, uint64

from cdv.cmds.util import parse_program

"""
This group of commands is for guessing the types of objects when you don't know what they are,
but also for building them from scratch and for modifying them once you have them completely loaded/built.
"""


@click.group("inspect", short_help="Inspect various data structures")
@click.option("-j", "--json", is_flag=True, help="Output the result as JSON")
@click.option("-b", "--bytes", is_flag=True, help="Output the result as bytes")
@click.option("-id", "--id", is_flag=True, help="Output the id of the object")
@click.option("-t", "--type", is_flag=True, help="Output the type of the object")
@click.pass_context
def inspect_cmd(ctx: click.Context, **kwargs) -> None:
    ctx.ensure_object(dict)
    for key, value in kwargs.items():
        ctx.obj[key] = value


# Every inspect command except the key related ones will call this when they're done
# The function allows the flags on "inspect" apply AFTER the objects have been successfully loaded
def inspect_callback(
    objs: list[Any],
    ctx: click.Context,
    id_calc: Callable = (lambda: None),
    type: str = "Unknown",
):
    # By default we return JSON
    if (not any([value for key, value in ctx.obj.items()])) or ctx.obj["json"]:
        if getattr(objs[0], "to_json_dict", None):
            print(json.dumps([obj.to_json_dict() for obj in objs]))
        else:
            pprint(f"Object of type {type} cannot be serialized to JSON")
    if ctx.obj["bytes"]:
        final_output = []
        for obj in objs:
            try:
                final_output.append(bytes(obj).hex())
            except AssertionError:
                final_output.append("None")  # This is for coins since coins overload the __bytes__ method
        pprint(final_output)
    if ctx.obj["id"]:
        pprint([id_calc(obj) for obj in objs])
    if ctx.obj["type"]:
        pprint([type for _ in objs])


# Utility functions


# If there's only one key, return the data on that key instead (for things like {'spend_bundle': {...}})
def json_and_key_strip(input: str) -> dict:
    json_dict: dict = json.loads(input)
    if len(json_dict.keys()) == 1:
        result: dict = json_dict[next(iter(json_dict.keys()))]
        return result  # mypy
    else:
        return json_dict


# Streamable objects can be in either bytes or JSON and we'll take them via CLI or file
def streamable_load(cls: Any, inputs: Iterable[Any]) -> list[Any]:
    # If we're receiving a group of objects rather than strings to parse, we're going to return them back as a list
    if inputs and not isinstance(next(iter(inputs)), str):
        for inst in inputs:
            assert isinstance(inst, cls)
        return list(inputs)

    input_objs: list[Any] = []
    for input in inputs:
        if "{" in input:  # If it's a JSON string
            json_dict = json_and_key_strip(input)
            parsed_obj = cls.from_json_dict(json_dict)
        elif "." in input:  # If it's a filename
            file_string = open(input).read()
            if "{" in file_string:  # If it's a JSON file
                json_dict = json_and_key_strip(file_string)
                parsed_obj = cls.from_json_dict(json_dict)
            else:  # If it's bytes in a file
                original_bytes = hexstr_to_bytes(file_string)
                parsed_obj = cls.from_bytes(original_bytes)
                assert bytes(parsed_obj) == original_bytes  # assert the serialization incase it was only a partial read
        else:  # If it's a byte string
            original_bytes = hexstr_to_bytes(input)
            parsed_obj = cls.from_bytes(original_bytes)
            assert bytes(parsed_obj) == original_bytes

        input_objs.append(parsed_obj)

    return input_objs


# Theoretically, every type of data should have it's command called if it's passed through this function
@inspect_cmd.command("any", short_help="Attempt to guess the type of the object before inspecting it")
@click.argument("objects", nargs=-1, required=False)
@click.pass_context
def inspect_any_cmd(ctx: click.Context, objects: tuple[str]):
    input_objects = []
    for obj in objects:
        in_obj: Any = obj
        # Try it as Streamable types
        for cls in [Coin, CoinSpend, WalletSpendBundle, CoinRecord]:
            try:
                in_obj = streamable_load(cls, [obj])[0]
                break
            except Exception:
                pass
        else:
            # Try it as some key stuff
            for cls in [G1Element, G2Element, PrivateKey]:
                try:
                    in_obj = cls.from_bytes(hexstr_to_bytes(obj))  # type: ignore
                    break
                except Exception:
                    pass
            else:
                # Try it as a Program
                try:
                    in_obj = parse_program(obj)
                except Exception:
                    pass

        input_objects.append(in_obj)

    for obj in input_objects:
        if type(obj) is str:
            print(f"Could not guess the type of {obj}")
        elif type(obj) is Coin:
            assert isinstance(obj, Coin)  # mypy otherwise complains that obj is a str
            do_inspect_coin_cmd(ctx, [obj])
        elif type(obj) is CoinSpend:
            assert isinstance(obj, CoinSpend)
            do_inspect_coin_spend_cmd(ctx, [obj])
        elif type(obj) is WalletSpendBundle:
            assert isinstance(obj, WalletSpendBundle)
            do_inspect_spend_bundle_cmd(ctx, [obj])
        elif type(obj) is CoinRecord:
            do_inspect_coin_record_cmd(ctx, [obj])
        elif type(obj) is Program:
            assert isinstance(obj, Program)
            do_inspect_program_cmd(ctx, [obj])
        elif type(obj) is G1Element:
            do_inspect_keys_cmd(ctx, public_key=obj)
        elif type(obj) is PrivateKey:
            do_inspect_keys_cmd(ctx, secret_key=obj)
        elif type(obj) is G2Element:
            print("That's a BLS aggregated signature")  # This is more helpful than just printing it back to them


"""
Most of the following commands are designed to also be available as importable functions and usable by the "any" cmd.
This is why there's the cmd/do_cmd pattern in all of them.
The objects they are inspecting can be a list of strings (from the cmd line), or a list of the object being inspected.
"""


@inspect_cmd.command("coins", short_help="Various methods for examining and calculating coin objects")
@click.argument("coins", nargs=-1, required=False)
@click.option("-pid", "--parent-id", help="The parent coin's ID")
@click.option("-ph", "--puzzle-hash", help="The tree hash of the KLVM puzzle that locks this coin")
@click.option("-a", "--amount", help="The amount of the coin")
@click.pass_context
def inspect_coin_cmd(ctx: click.Context, coins: tuple[str], **kwargs):
    do_inspect_coin_cmd(ctx, coins, **kwargs)


def do_inspect_coin_cmd(
    ctx: click.Context,
    coins: Union[tuple[str], list[Coin]],
    print_results: bool = True,
    **kwargs,
) -> list[Coin]:
    # If this is built from the command line and all relevant arguments are there
    if kwargs and all([kwargs[key] for key in kwargs.keys()]):
        coin_objs: list[Coin] = [
            Coin(
                bytes32.from_hexstr(kwargs["parent_id"]),
                bytes32.from_hexstr(kwargs["puzzle_hash"]),
                uint64(kwargs["amount"]),
            )
        ]
    elif not kwargs or not any([kwargs[key] for key in kwargs.keys()]):
        try:
            coin_objs = streamable_load(Coin, coins)
        except Exception as e:
            print(f"One or more of the specified objects was not a coin: {e}")
            sys.exit(1)
    else:
        print("Invalid arguments specified.")
        sys.exit(1)

    if print_results:
        inspect_callback(coin_objs, ctx, id_calc=(lambda e: e.name().hex()), type="Coin")

    return coin_objs


@inspect_cmd.command(
    "spends",
    short_help="Various methods for examining and calculating CoinSpend objects",
)
@click.argument("spends", nargs=-1, required=False)
@click.option("-c", "--coin", help="The coin to spend (replaces -pid, -ph, -a)")
@click.option("-pid", "--parent-id", help="The parent coin's ID")
@click.option(
    "-ph",
    "--puzzle-hash",
    help="The tree hash of the KLVM puzzle that locks the coin being spent",
)
@click.option("-a", "--amount", help="The amount of the coin being spent")
@click.option("-pr", "--puzzle-reveal", help="The program that is hashed into this coin")
@click.option("-s", "--solution", help="The attempted solution to the puzzle")
@click.option("-ec", "--cost", is_flag=True, help="Print the KLVM cost of the spend")
@click.option(
    "--ignore-byte-cost",
    is_flag=True,
    help="Ignore the puzzle reveal cost when examining a spend (mimics potential compression)",
)
@click.pass_context
def inspect_coin_spend_cmd(ctx: click.Context, spends: tuple[str], **kwargs):
    do_inspect_coin_spend_cmd(ctx, spends, **kwargs)


def do_inspect_coin_spend_cmd(
    ctx: click.Context,
    spends: Union[tuple[str], list[CoinSpend]],
    print_results: bool = True,
    **kwargs,
) -> list[CoinSpend]:
    cost_flag: bool = False
    ignore_byte_cost: bool = False
    if kwargs:
        # These args don't really fit with the logic below so we're going to store and delete them
        cost_flag = kwargs["cost"]
        ignore_byte_cost = kwargs["ignore_byte_cost"]
        del kwargs["cost"]
        del kwargs["ignore_byte_cost"]
    # If this is being built from the command line and the two required args are there
    if kwargs and all([kwargs["puzzle_reveal"], kwargs["solution"]]):
        # If they specified the coin components
        if (not kwargs["coin"]) and all([kwargs["parent_id"], kwargs["puzzle_hash"], kwargs["amount"]]):
            coin_spend_objs: list[CoinSpend] = [
                make_spend(
                    Coin(
                        bytes32.from_hexstr(kwargs["parent_id"]),
                        bytes32.from_hexstr(kwargs["puzzle_hash"]),
                        uint64(kwargs["amount"]),
                    ),
                    parse_program(kwargs["puzzle_reveal"]),
                    parse_program(kwargs["solution"]),
                )
            ]
        # If they specifed a coin object to parse
        elif kwargs["coin"]:
            coin_spend_objs = [
                make_spend(
                    do_inspect_coin_cmd(ctx, [kwargs["coin"]], print_results=False)[0],
                    parse_program(kwargs["puzzle_reveal"]),
                    parse_program(kwargs["solution"]),
                )
            ]
        else:
            print("Invalid arguments specified.")
    elif not kwargs or not any([kwargs[key] for key in kwargs.keys()]):
        try:
            coin_spend_objs = streamable_load(CoinSpend, spends)
        except Exception as e:
            print(f"One or more of the specified objects was not a coin spend: {e}")
            sys.exit(1)
    else:
        print("Invalid arguments specified.")
        sys.exit(1)

    if print_results:
        inspect_callback(
            coin_spend_objs,
            ctx,
            id_calc=(lambda e: e.coin.name().hex()),
            type="CoinSpend",
        )
        # We're going to print some extra stuff if they wanted to see the cost
        if cost_flag:
            for coin_spend in coin_spend_objs:
                program: BlockGenerator = simple_solution_generator(WalletSpendBundle([coin_spend], G2Element()))
                npc_result: NPCResult = get_name_puzzle_conditions(
                    program,
                    INFINITE_COST,
                    height=DEFAULT_CONSTANTS.SOFT_FORK6_HEIGHT,  # so that all opcodes are available
                    mempool_mode=True,
                    constants=DEFAULT_CONSTANTS,
                )
                cost = int(0 if npc_result.conds is None else npc_result.conds.cost)
                if ignore_byte_cost:
                    cost -= len(bytes(coin_spend.puzzle_reveal)) * DEFAULT_CONSTANTS.COST_PER_BYTE
                print(f"Cost: {cost}")

    return coin_spend_objs


@inspect_cmd.command(
    "spendbundles",
    short_help="Various methods for examining and calculating SpendBundle objects",
)
@click.argument("bundles", nargs=-1, required=False)
@click.option("-s", "--spend", multiple=True, help="A coin spend object to add to the bundle")
@click.option(
    "-as",
    "--aggsig",
    multiple=True,
    help="A BLS signature to aggregate into the bundle (can be used more than once)",
)
@click.option("-db", "--debug", is_flag=True, help="Show debugging information about the bundles")
@click.option(
    "-sd",
    "--signable_data",
    is_flag=True,
    help="Print the data that needs to be signed in the bundles",
)
@click.option(
    "-n",
    "--network",
    default="mainnet",
    show_default=True,
    help="The network this spend bundle will be pushed to (for AGG_SIG_ME)",
)
@click.option("-ec", "--cost", is_flag=True, help="Print the KLVM cost of the entire bundle")
@click.option(
    "--ignore-byte-cost",
    is_flag=True,
    help="Ignore the puzzle reveal cost when examining a spend (mimics potential compression)",
)
@click.pass_context
def inspect_spend_bundle_cmd(ctx: click.Context, bundles: tuple[str], **kwargs):
    do_inspect_spend_bundle_cmd(ctx, bundles, **kwargs)


def do_inspect_spend_bundle_cmd(
    ctx: click.Context,
    bundles: Union[tuple[str], list[WalletSpendBundle]],
    print_results: bool = True,
    **kwargs,
) -> list[WalletSpendBundle]:
    # If this is from the command line and they've specified at lease one spend to parse
    if kwargs and (len(kwargs["spend"]) > 0):
        if len(kwargs["aggsig"]) > 0:
            sig: G2Element = AugSchemeMPL.aggregate(
                [G2Element.from_bytes(hexstr_to_bytes(sig)) for sig in kwargs["aggsig"]]
            )
        else:
            sig = G2Element()
        spend_bundle_objs: list[WalletSpendBundle] = [
            WalletSpendBundle(
                do_inspect_coin_spend_cmd(ctx, kwargs["spend"], print_results=False),
                sig,
            )
        ]
    else:
        try:
            spend_bundle_objs = streamable_load(WalletSpendBundle, bundles)
        except Exception as e:
            print(f"One or more of the specified objects was not a spend bundle: {e}")
            sys.exit(1)

    if print_results:
        inspect_callback(
            spend_bundle_objs,
            ctx,
            id_calc=(lambda e: e.name().hex()),
            type="WalletSpendBundle",
        )
        # We're going to print some extra stuff if they've asked for it.
        if kwargs:
            if kwargs["cost"]:
                for spend_bundle in spend_bundle_objs:
                    program: BlockGenerator = simple_solution_generator(spend_bundle)
                    npc_result: NPCResult = get_name_puzzle_conditions(
                        program,
                        INFINITE_COST,
                        height=DEFAULT_CONSTANTS.SOFT_FORK6_HEIGHT,  # so that all opcodes are available
                        mempool_mode=True,
                        constants=DEFAULT_CONSTANTS,
                    )
                    cost = int(0 if npc_result.conds is None else npc_result.conds.cost)
                    if kwargs["ignore_byte_cost"]:
                        for coin_spend in spend_bundle.coin_spends:
                            cost -= len(bytes(coin_spend.puzzle_reveal)) * DEFAULT_CONSTANTS.COST_PER_BYTE
                    print(f"Cost: {cost}")
            if kwargs["debug"]:
                print("")
                print("Debugging Information")
                print("---------------------")
                config: dict = load_config(DEFAULT_ROOT_PATH, "config.yaml")
                genesis_challenge: str = config["network_overrides"]["constants"][kwargs["network"]][
                    "GENESIS_CHALLENGE"
                ]
                for bundle in spend_bundle_objs:
                    bundle.debug(agg_sig_additional_data=bytes32(hexstr_to_bytes(genesis_challenge)))
            if kwargs["signable_data"]:
                print("")
                print("Public Key/Message Pairs")
                print("------------------------")
                pkm_dict: dict[str, list[bytes]] = {}
                for obj in spend_bundle_objs:
                    for coin_spend in obj.coin_spends:
                        conditions_dict = conditions_dict_for_solution(
                            coin_spend.puzzle_reveal.to_program(), coin_spend.solution.to_program(), INFINITE_COST
                        )
                        if conditions_dict is None:
                            print(f"Generating conditions failed, con: {conditions_dict}")
                        else:
                            config = load_config(DEFAULT_ROOT_PATH, "config.yaml")
                            genesis_challenge = config["network_overrides"]["constants"][kwargs["network"]][
                                "GENESIS_CHALLENGE"
                            ]
                            for pk, msg in pkm_pairs_for_conditions_dict(
                                conditions_dict,
                                coin_spend.coin,
                                hexstr_to_bytes(genesis_challenge),
                            ):
                                if str(pk) in pkm_dict:
                                    pkm_dict[str(pk)].append(msg)
                                else:
                                    pkm_dict[str(pk)] = [msg]
                # This very deliberately prints identical messages multiple times
                for pk_str, msgs in pkm_dict.items():
                    print(f"{pk_str}: ")
                    for msg in msgs:
                        print(f"\t- {msg.hex()}")

    return spend_bundle_objs


@inspect_cmd.command(
    "coinrecords",
    short_help="Various methods for examining and calculating CoinRecord objects",
)
@click.argument("records", nargs=-1, required=False)
@click.option("-c", "--coin", help="The coin to spend (replaces -pid, -ph, -a)")
@click.option("-pid", "--parent-id", help="The parent coin's ID")
@click.option(
    "-ph",
    "--puzzle-hash",
    help="The tree hash of the KLVM puzzle that locks the coin being spent",
)
@click.option("-a", "--amount", help="The amount of the coin being spent")
@click.option(
    "-cb",
    "--coinbase",
    is_flag=True,
    help="Is this coin generated as a farming reward?",
)
@click.option(
    "-ci",
    "--confirmed-block-index",
    help="The block index in which this coin was created",
)
@click.option(
    "-si",
    "--spent-block-index",
    default=0,
    show_default=True,
    type=int,
    help="The block index in which this coin was spent",
)
@click.option(
    "-t",
    "--timestamp",
    help="The timestamp of the block in which this coin was created",
)
@click.pass_context
def inspect_coin_record_cmd(ctx: click.Context, records: tuple[str], **kwargs):
    do_inspect_coin_record_cmd(ctx, records, **kwargs)


def do_inspect_coin_record_cmd(
    ctx: click.Context,
    records: Union[tuple[str], list[CoinRecord]],
    print_results: bool = True,
    **kwargs,
) -> list[CoinRecord]:
    # If we're building this from the command line and we have the two arguements we forsure need
    if kwargs and all([kwargs["confirmed_block_index"], kwargs["timestamp"]]):
        # If they've specified the components of a coin
        if (not kwargs["coin"]) and all([kwargs["parent_id"], kwargs["puzzle_hash"], kwargs["amount"]]):
            coin_record_objs: list[CoinRecord] = [
                CoinRecord(
                    Coin(
                        bytes32.from_hexstr(kwargs["parent_id"]),
                        bytes32.from_hexstr(kwargs["puzzle_hash"]),
                        uint64(kwargs["amount"]),
                    ),
                    kwargs["confirmed_block_index"],
                    kwargs["spent_block_index"],
                    kwargs["coinbase"],
                    kwargs["timestamp"],
                )
            ]
        # If they've specified a coin to parse
        elif kwargs["coin"]:
            coin_record_objs = [
                CoinRecord(
                    do_inspect_coin_cmd(ctx, (kwargs["coin"],), print_results=False)[0],
                    kwargs["confirmed_block_index"],
                    kwargs["spent_block_index"],
                    kwargs["coinbase"],
                    kwargs["timestamp"],
                )
            ]
        else:
            print("Invalid arguments specified.")
    elif not kwargs or not any([kwargs[key] for key in kwargs.keys()]):
        try:
            coin_record_objs = streamable_load(CoinRecord, records)
        except Exception as e:
            print(f"One or more of the specified objects was not a coin record: {e}")
            sys.exit(1)
    else:
        print("Invalid arguments specified.")
        sys.exit(1)

    if print_results:
        inspect_callback(
            coin_record_objs,
            ctx,
            id_calc=(lambda e: e.coin.name().hex()),
            type="CoinRecord",
        )

    return coin_record_objs


@inspect_cmd.command("programs", short_help="Various methods for examining KLVM Program objects")
@click.argument("programs", nargs=-1, required=False)
@click.pass_context
def inspect_program_cmd(ctx: click.Context, programs: tuple[str], **kwargs):
    do_inspect_program_cmd(ctx, programs, **kwargs)


def do_inspect_program_cmd(
    ctx: click.Context,
    programs: Union[tuple[str], list[Program]],
    print_results: bool = True,
    **kwargs,
) -> list[Program]:
    try:
        program_objs: list[Program] = [parse_program(prog) for prog in programs]
    except Exception:
        print("One or more of the specified objects was not a Program")
        sys.exit(1)

    if print_results:
        inspect_callback(
            program_objs,
            ctx,
            id_calc=(lambda e: e.get_tree_hash().hex()),
            type="Program",
        )

    return program_objs


@inspect_cmd.command("keys", short_help="Various methods for examining and generating BLS Keys")
@click.option("-pk", "--public-key", help="A BLS public key")
@click.option("-sk", "--secret-key", help="The secret key from which to derive the public key")
@click.option("-m", "--mnemonic", help="A 24 word mnemonic from which to derive the secret key")
@click.option("-r", "--random", is_flag=True, help="Generate a random set of keys")
@click.option("-hd", "--hd-path", help="Enter the HD path in the form 'm/12381/8444/n/n'")
@click.option(
    "-t",
    "--key-type",
    type=click.Choice(["farmer", "pool", "wallet", "local", "backup", "owner", "auth"]),
    help="Automatically use a chik defined HD path for a specific service",
)
@click.option(
    "-sy",
    "--synthetic",
    is_flag=True,
    help="Use a hidden puzzle hash (-ph) to calculate a synthetic secret/public key",
)
@click.option(
    "-ph",
    "--hidden-puzhash",
    default=DEFAULT_HIDDEN_PUZZLE_HASH.hex(),
    show_default=False,
    help="The hidden puzzle to use when calculating a synthetic key",
)
@click.pass_context
def inspect_keys_cmd(ctx: click.Context, **kwargs):
    do_inspect_keys_cmd(ctx, **kwargs)


def do_inspect_keys_cmd(ctx: click.Context, print_results: bool = True, **kwargs):
    sk: Optional[PrivateKey] = None
    pk: G1Element = G1Element()
    path: str = "m"
    # If we're receiving this from the any command
    if len(kwargs) == 1:
        if "secret_key" in kwargs:
            sk = kwargs["secret_key"]
            assert sk is not None
            pk = sk.get_g1()
        elif "public_key" in kwargs:
            pk = kwargs["public_key"]
    else:
        condition_list = [
            kwargs["public_key"],
            kwargs["secret_key"],
            kwargs["mnemonic"],
            kwargs["random"],
        ]

        # This a construct to ensure there is exactly one of these conditions set
        def one_or_zero(value):
            return 1 if value else 0

        if sum([one_or_zero(condition) for condition in condition_list]) == 1:
            if kwargs["public_key"]:
                sk = None
                pk = G1Element.from_bytes(hexstr_to_bytes(kwargs["public_key"]))
            elif kwargs["secret_key"]:
                sk = PrivateKey.from_bytes(hexstr_to_bytes(kwargs["secret_key"]))
                pk = sk.get_g1()
            elif kwargs["mnemonic"]:
                seed = mnemonic_to_seed(kwargs["mnemonic"])
                sk = AugSchemeMPL.key_gen(seed)
                pk = sk.get_g1()
            elif kwargs["random"]:
                sk = AugSchemeMPL.key_gen(mnemonic_to_seed(bytes_to_mnemonic(token_bytes(32))))
                pk = sk.get_g1()

            list_path: list[int] = []
            if kwargs["hd_path"] and (kwargs["hd_path"] != "m"):
                list_path = [uint32(int(i)) for i in kwargs["hd_path"].split("/") if i != "m"]
            elif kwargs["key_type"]:
                case = kwargs["key_type"]
                if case == "farmer":
                    list_path = [12381, 8444, 0, 0]
                if case == "pool":
                    list_path = [12381, 8444, 1, 0]
                if case == "wallet":
                    list_path = [12381, 8444, 2, 0]
                if case == "local":
                    list_path = [12381, 8444, 3, 0]
                if case == "backup":
                    list_path = [12381, 8444, 4, 0]
                if case == "owner":
                    list_path = [12381, 8444, 5, 0]
                if case == "auth":
                    list_path = [12381, 8444, 6, 0]
            if list_path:
                assert sk is not None
                sk = _derive_path(sk, list_path)
                pk = sk.get_g1()
                path = "m/" + "/".join([str(e) for e in path])

            if kwargs["synthetic"]:
                if sk:
                    sk = calculate_synthetic_secret_key(sk, bytes32.from_hexstr(kwargs["hidden_puzhash"]))
                pk = calculate_synthetic_public_key(pk, bytes32.from_hexstr(kwargs["hidden_puzhash"]))
        else:
            print("Invalid arguments specified.")

    if sk:
        print(f"Secret Key: {bytes(sk).hex()}")
    print(f"Public Key: {pk!s}")
    print(f"Fingerprint: {pk.get_fingerprint()!s}")
    print(f"HD Path: {path}")


# This class is necessary for being able to handle parameters in order, rather than grouped by name
class OrderedParamsCommand(click.Command):
    _options: ClassVar[list] = []

    def parse_args(self, ctx, args):
        # run the parser for ourselves to preserve the passed order
        parser = self.make_parser(ctx)
        opts, _, param_order = parser.parse_args(args=list(args))
        for param in param_order:
            if param.name != "help":
                type(self)._options.append((param, opts[param.name].pop(0)))

        # return "normal" parse results
        return super().parse_args(ctx, args)


@inspect_cmd.command(
    "signatures",
    cls=OrderedParamsCommand,
    short_help="Various methods for examining and creating BLS aggregated signatures",
)
@click.option("-sk", "--secret-key", multiple=True, help="A secret key to sign a message with")
@click.option(
    "-t",
    "--utf-8",
    multiple=True,
    help="A UTF-8 message to be signed with the specified secret key",
)
@click.option(
    "-b",
    "--bytes",
    multiple=True,
    help="A hex message to be signed with the specified secret key",
)
@click.option("-sig", "--aggsig", multiple=True, help="A signature to be aggregated")
@click.pass_context
def inspect_sigs_cmd(ctx: click.Context, **kwargs):
    do_inspect_sigs_cmd(ctx, **kwargs)


# This command sort of works like a script:
# Whenever you use a parameter, it changes some state,
# at the end it returns the result of running those parameters in that order.
def do_inspect_sigs_cmd(ctx: click.Context, print_results: bool = True, **kwargs) -> G2Element:
    base = G2Element()
    sk: Optional[PrivateKey] = None
    for param, value in OrderedParamsCommand._options:
        if param.name == "secret_key":
            sk = PrivateKey.from_bytes(hexstr_to_bytes(value))
        elif param.name == "aggsig":
            new_sig = G2Element.from_bytes(hexstr_to_bytes(value))
            base = AugSchemeMPL.aggregate([base, new_sig])
        elif sk:
            if param.name == "utf_8":
                new_sig = AugSchemeMPL.sign(sk, bytes(value, "utf-8"))
                base = AugSchemeMPL.aggregate([base, new_sig])
            if param.name == "bytes":
                new_sig = AugSchemeMPL.sign(sk, hexstr_to_bytes(value))
                base = AugSchemeMPL.aggregate([base, new_sig])

    if print_results:
        print(str(base))

    return base
