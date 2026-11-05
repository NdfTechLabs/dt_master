#!/usr/bin/env python3
"""Default-preserving, amd64 browser-only inner-sandbox seccomp additions."""

import copy
import hashlib
import json


BASELINE_SHA256 = "536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74"
NEWUSER = 0x10000000
NEWPID = 0x20000000
NEWNET = 0x40000000
ALLOWED_NAMESPACE_FLAGS = NEWUSER | NEWPID | NEWNET
FORBIDDEN_CLONE_NAMESPACES = 0x00020000 | 0x02000000 | 0x04000000 | 0x08000000 | 0x80


def browser_profile(raw, architecture="x86_64"):
    if architecture != "x86_64":
        raise RuntimeError("Browser policy reviewed for amd64 only")
    if hashlib.sha256(raw).hexdigest() != BASELINE_SHA256:
        raise RuntimeError("Docker policy baseline checksum mismatch")
    baseline = json.loads(raw)
    if baseline.get("defaultAction") != "SCMP_ACT_ERRNO" or baseline.get("defaultErrnoRet") != 1:
        raise RuntimeError("Unexpected baseline default")
    result = copy.deepcopy(baseline)
    additions = [
        {"names": ["clone"], "action": "SCMP_ACT_ALLOW", "includes": {"arches": ["amd64"]},
         "args": [{"index": 0, "value": FORBIDDEN_CLONE_NAMESPACES, "valueTwo": 0, "op": "SCMP_CMP_MASKED_EQ"}],
         "comment": "UI sandbox only: user/PID/net namespace creation, not mount/UTS/IPC/cgroup/time namespaces"},
        {"names": ["unshare"], "action": "SCMP_ACT_ALLOW", "includes": {"arches": ["amd64"]},
         "args": [{"index": 0, "value": (~ALLOWED_NAMESPACE_FLAGS) & ((1 << 64) - 1), "valueTwo": 0, "op": "SCMP_CMP_MASKED_EQ"}],
         "comment": "UI sandbox only: unshare flags restricted to user/PID/net namespaces"},
    ]
    additions += [{"names": ["setns"], "action": "SCMP_ACT_ALLOW", "includes": {"arches": ["amd64"]},
                   "args": [{"index": 1, "value": kind, "op": "SCMP_CMP_EQ"}],
                   "comment": "UI sandbox only: typed user/PID/net namespace; kernel target-namespace permission checks still apply"}
                  for kind in (NEWUSER, NEWPID, NEWNET)]
    additions.append({"names": ["chroot"], "action": "SCMP_ACT_ALLOW", "includes": {"arches": ["amd64"]},
                      "comment": "UI inner filesystem sandbox; no outer SYS_CHROOT capability granted"})
    result["syscalls"].extend(additions)
    if result["syscalls"][:len(baseline["syscalls"])] != baseline["syscalls"]:
        raise RuntimeError("Baseline rules changed")
    return result, additions
