#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["eth-abi>=5,<6"]
# ///
"""Generate the chain-execution transactions for the v29.5 verifier upgrade.
Standalone: the upgrade calldata is baked in below and checked against the
`upgradeCutHash` the CTM actually has registered, so nothing outside this file
is read. Only the chain id is needed — the diamond proxy, ChainAdmin and sender
come from the Era CTM. Chains on another CTM are refused.
    uv run gen_chain_upgrade.py 232                  # deps handled automatically
    pip install eth-abi && python3 gen_chain_upgrade.py 232
Env: MAINNET_RPC (default https://eth.drpc.org)
"""
import argparse, json, os, sys, time, urllib.request
from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

BRIDGEHUB = "0x303a465B659cBB0ab36eE643eA362c509EEb5213"
# This upgrade exists only on the Era CTM, so the CTM is pinned rather than
# resolved per chain; the Bridgehub decides whether a chain belongs to it.
ERA_CTM = "0xc2eE6b6af7d616f6e27ce7F4A451Aedc2b0F5f5C"
SERVER_NOTIFIER = "0xfca808A744735D9919EEBe4660B8Fd897456Ce31"
FROM_VERSION, TO_VERSION = 0x1D00000004, 0x1D00000005

# upgradeChainFromVersion(FROM_VERSION, cut) for the v29.5 verifier upgrade.
# Chain-agnostic: the CTM registers one cut per old protocol version, so these
# exact bytes apply to every Era CTM chain. Verified against upgradeCutHash().
UPGRADE_CALLDATA = bytes.fromhex("".join("""
    fc57565f0000000000000000000000000000000000000000000000000000001d00000004
    000000000000000000000000000000000000000000000000000000000000004000000000
    000000000000000000000000000000000000000000000000000000600000000000000000
    000000008f7dd88c2435abe3f585bfb32eb2394d397af0aa000000000000000000000000
    000000000000000000000000000000000000008000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    0000000000000000000004e416ef13030000000000000000000000000000000000000000
    000000000000000000000020000000000000000000000000000000000000000000000000
    000000000000018000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000047fc5273145e053a18c0bbf6d88f8d6d573c3d0e0000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000480000000000000000000000000000000000000000000000000
    00000000000004a000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000001d00000005
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000260000000000000000000000000000000000000000000000000
    000000000000028000000000000000000000000000000000000000000000000000000000
    000002a000000000000000000000000000000000000000000000000000000000000002c0
    00000000000000000000000000000000000000000000000000000000000002e000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    000000000000000000000000000000000000000000000000000000000000000000000000
    0000000000000000000000000000000000000000000000000000000000000000
""".split()))


def sel(sig):
    return keccak(text=sig)[:4]


def call(to, sig, arg_types=(), args=(), ret="address"):
    data = "0x" + (sel(sig) + encode(list(arg_types), list(args))).hex()
    req = urllib.request.Request(
        os.environ.get("MAINNET_RPC", "https://eth.drpc.org"),
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                    "params": [{"to": to, "data": data}, "latest"]}).encode(),
        # some public RPCs 403 urllib's default User-Agent
        {"Content-Type": "application/json", "User-Agent": "curl/8"})
    for attempt in range(5):  # public RPCs rate-limit; back off rather than die mid-run
        try:
            body = urllib.request.urlopen(req, timeout=30).read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == 4:
                sys.exit(f"RPC error {e.code} calling {sig}: {e.reason}")
            time.sleep(2 * (attempt + 1))
    res = json.loads(body)
    if "error" in res:
        sys.exit(f"eth_call {sig} on {to} failed: {res['error']}")
    return decode([ret], bytes.fromhex(res["result"][2:]))[0]


def multicall(target, data):
    """ChainAdmin.multicall([(target, 0, data)], requireSuccess=true)"""
    args = encode(["(address,uint256,bytes)[]", "bool"], [[(target, 0, data)], True])
    return "0x" + (sel("multicall((address,uint256,bytes)[],bool)") + args).hex()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("chain_id", type=int)
    p.add_argument("--timestamp", default="1", help="upgrade timestamp; 1 = asap (0 is rejected)")
    p.add_argument("--sender", help="override the tx sender (for admins without owner())")
    p.add_argument("-o", "--out", help="write here instead of stdout")
    a = p.parse_args()

    # Only the Bridgehub can say which CTM owns a chain: the Era CTM still answers
    # getZKChain/getProtocolVersion for chains that have moved to another CTM.
    ctm = call(BRIDGEHUB, "chainTypeManager(uint256)", ["uint256"], [a.chain_id])
    if ctm.lower() != ERA_CTM.lower():
        sys.exit(f"chain {a.chain_id} is not an Era CTM chain (Bridgehub says "
                 f"{ctm}) — this upgrade only exists on Era CTM {ERA_CTM}")

    # The baked-in cut must be the one governance registered for FROM_VERSION.
    # abi.encode(cut) is the offset word plus the cut body, i.e. the calldata tail.
    want = call(ERA_CTM, "upgradeCutHash(uint256)", ["uint256"], [FROM_VERSION], ret="bytes32")
    if keccak((32).to_bytes(32, "big") + UPGRADE_CALLDATA[68:]) != want:
        sys.exit(f"baked-in cut does not match upgradeCutHash({FROM_VERSION:#x}) = 0x{want.hex()} "
                 f"— the CTM has a different upgrade registered")

    # The cut only applies to a chain sitting exactly on FROM_VERSION.
    current = call(ERA_CTM, "getProtocolVersion(uint256)", ["uint256"], [a.chain_id], ret="uint256")
    if current != FROM_VERSION:
        sys.exit(f"error: chain {a.chain_id} is at protocol version {current:#x}, "
                 f"expected {FROM_VERSION:#x} — this upgrade does not apply")

    diamond = call(ERA_CTM, "getZKChain(uint256)", ["uint256"], [a.chain_id])
    admin = call(ERA_CTM, "getChainAdmin(uint256)", ["uint256"], [a.chain_id])
    # Most chains use the standard ChainAdmin; some (e.g. Cronos) have a custom admin with no owner().
    sender = a.sender or call(admin, "owner()")

    out = [
        {"network": "mainnet", "from": to_checksum_address(sender), "to": to_checksum_address(admin),
         "data": multicall(SERVER_NOTIFIER, sel("setUpgradeTimestamp(uint256,uint256)")
                           + encode(["uint256", "uint256"], [a.chain_id, int(a.timestamp)])),
         "value": "0", "tag": "server-notify", "valueToMint": "1000",
         "description": "setUpgradeTimestamp (ChainAdmin multicall -> ServerNotifier)"},
        {"network": "mainnet", "from": to_checksum_address(sender), "to": to_checksum_address(admin),
         "data": multicall(to_checksum_address(diamond), UPGRADE_CALLDATA),
         "value": "0", "tag": "chain-upgrade", "valueToMint": "1000",
         "description": f"upgradeChainFromVersion ({FROM_VERSION:#x} -> {TO_VERSION:#x}, "
                        f"ChainAdmin multicall -> chain {a.chain_id} DiamondProxy)"},
    ]
    text = json.dumps(out, indent=4) + "\n"
    if a.out:
        open(a.out, "w").write(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()