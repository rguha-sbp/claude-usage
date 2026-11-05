"""dashboard/office-sim.js, office-floor.js, office-themes.js, office-cast.js
and office-rail.js are DOM-free on purpose so the state machines, the floor
layout, the themes and the model tiers can be driven under node
(tests/office_*_check.js). Skipped when node is not installed."""
import json
import re
import shutil
import subprocess

import pytest

from usage.config import REPO_ROOT

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _run(script):
    proc = subprocess.run(["node", str(REPO_ROOT / "tests" / script)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@needs_node
def test_office_simulation_behaves():
    report = _run("office_sim_check.js")
    interns = report["interns"]
    assert report["agentsLeft"] == ["a", "b", "c", "e"]
    assert report["seen"]["d"]["walking"] > 0               # a sleeper walks only once its session ends
    assert report["seen"]["c"]["walking"] > 0               # working leads take breaks
    assert report["bPose"] == "dozing" and report["bAtSeat"]
    assert report["maxSpeed"] <= 2.35                       # the swift tier's ceiling
    assert report["top"]["e"] > report["top"]["b"]          # Haiku outpaces Sonnet
    assert report["crossProject"] and report["loungeBubble"]
    assert report["canteenSpotDist"] < 0.2                  # loungers stand on their spot
    assert interns == {"preAtSlot": True, "arrived": True, "handed": True, "left": True,
                       "leadStays": True, "overflow": 4, "nestedParent": "L/new"}


@needs_node
def test_model_tiers_and_daylight():
    report = _run("office_cast_check.js")
    assert report["tiers"] == ["lead", "muse", "core", "swift"]
    assert set(report["hours"]) == {"dawn", "day", "dusk", "night"}
    assert sum(report["hours"].values()) == 24
    assert report["families"] == ["read", "edit", "shell", "web", "delegate", "mcp", "think"]


@needs_node
def test_floor_layout_holds_for_every_theme():
    """The floor check lays out 1, 4 and 12 projects in every theme, checks
    the plan with its own collision code, then drives the simulation over
    the real places and fails on anyone who walks through furniture."""
    report = _run("office_floor_check.js")
    assert set(report["floors"]) == {"%d/%s" % (n, t) for n in (1, 4, 12) for t in ("studio", "loft", "greenhouse")}
    assert report["themesDiffer"]
    assert all(count > 50 for count in report["decor"].values())  # a decorated floor, not a bare one
    for seen in report["sim"].values():
        assert seen["canteen"] and seen["sofa"] and seen["pod"] and seen["door"]
        assert seen["relaid"].endswith("seat moved")             # a re-layout during a break moved the walker's desk
    # sessions ending at the canteen, at another row's cooler and mid-walk
    # leave on legs that cross no desk, partition or decor
    assert all(count > 0 for count in report["leavers"].values())


def _members(name, const):
    """The member names of `const <const> = { ... };` in a dashboard file."""
    source = (REPO_ROOT / "dashboard" / name).read_text()
    start = source.index("const %s = {" % const)
    block = source[start:source.index("\n};", start)]
    return set(re.findall(r"^  (\w+)\(", block, flags=re.M)) | set(re.findall(r"^  (\w+):", block, flags=re.M))


@needs_node
def test_themes_only_name_what_the_scenery_draws():
    report = _run("office_themes_check.js")
    assert report["themes"] == ["studio", "loft", "greenhouse"]
    assert set(report["patterns"]) <= _members("office-scenery.js", "PATTERNS")
    assert set(report["partitions"]) <= _members("office-scenery.js", "PARTITIONS")
    assert set(report["kinds"]) <= _members("office-decor.js", "BUILDERS")


@needs_node
def test_agents_rail_markup():
    report = _run("office_rail_check.js")
    assert report["rows"] == 4 and report["order"] == ["a:1", "b:2", "c:1", "orphan:1"]
