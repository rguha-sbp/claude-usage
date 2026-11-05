"""dashboard/office-sim.js, office-world.js and office-cast.js are DOM-free on
purpose so the state machines, the campus layout and the model tiers can be
driven under node (tests/office_*_check.js). Skipped when node is not
installed."""
import json
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
    assert report["seen"]["c"].get("walking") is None       # working sessions stay seated
    assert report["seen"]["a"]["walking"] > 0 and report["seen"]["d"]["walking"] > 0
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
def test_world_layout_and_scene():
    report = _run("office_world_check.js")
    assert report["blocks"] == 2 and report["canteen"] >= 2
    assert report["width"] > 0 and report["height"] > 0
    # the simulation driven over the real campus, not a hand-written fixture
    assert report["reachedCanteen"] and report["home"] > 0
    assert report["podOk"]


@needs_node
def test_figures_are_clean_strings():
    report = _run("office_figures_check.js")
    assert report["lead"] > 500 and report["intern"] > 500


@needs_node
def test_agents_rail_markup():
    report = _run("office_rail_check.js")
    assert report["rows"] == 4 and report["order"] == ["a:1", "b:2", "c:1", "orphan:1"]
