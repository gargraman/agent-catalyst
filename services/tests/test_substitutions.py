"""M1 (RED) — preflight capability substitutions (02 §5, 02L §6 rule table).

Every downgrade needed to run a request on a model must surface as a Substitution — never silent
(H2: "capability substitutions recorded"). capability_substitution is also a DRIFT_TRIGGER (05 §5).
"""
from __future__ import annotations

import pytest

from runtime.providers.ir import NormalizedRequest
from runtime.providers.profiles import (
    VALID_ACTIONS,
    CapabilityProfile,
    preflight_substitutions,
)

ALL_FEATURES = {
    "structured_output": True, "forced_tool_choice": True,
    "reasoning_control": True, "prompt_caching": True,
}


def _profile(features):
    return CapabilityProfile(
        provider="p", model="m", api_version=None, pricing={}, limits={}, features=features
    )


def _req(**over):
    base = dict(provider="p", model="m", api_version=None, messages=[], system=None, params={})
    base.update(over)
    return NormalizedRequest(**base)


def test_no_substitutions_when_profile_supports_everything():
    req = _req(response_format={"type": "json"}, tool_choice={"name": "t"},
              reasoning={"effort": "high"}, cache_hints=[{"at": 0}])
    assert preflight_substitutions(_profile(ALL_FEATURES), req) == []


def test_plain_request_needs_no_substitutions():
    assert preflight_substitutions(_profile({}), _req()) == []


def test_structured_output_emulated_when_unsupported():
    subs = preflight_substitutions(_profile({}), _req(response_format={"type": "json"}))
    assert len(subs) == 1
    assert subs[0].feature == "structured_output"
    assert subs[0].action == "emulated_via_prompt"


def test_forced_tool_choice_emulated_when_unsupported():
    subs = preflight_substitutions(_profile({}), _req(tool_choice={"name": "t"}))
    assert [s.feature for s in subs] == ["forced_tool_choice"]
    assert subs[0].action == "emulated_via_prompt"


def test_string_tool_choice_is_not_forced():
    # tool_choice as a string ("auto"/"none") is not a forced tool → no substitution
    assert preflight_substitutions(_profile({}), _req(tool_choice="auto")) == []


def test_reasoning_control_dropped_when_unsupported():
    subs = preflight_substitutions(_profile({}), _req(reasoning={"effort": "high"}))
    assert [s.feature for s in subs] == ["reasoning_control"]
    assert subs[0].action == "dropped"


def test_prompt_caching_dropped_when_unsupported():
    subs = preflight_substitutions(_profile({}), _req(cache_hints=[{"at": 0}]))
    assert [s.feature for s in subs] == ["prompt_caching"]
    assert subs[0].action == "dropped"


def test_multiple_downgrades_all_recorded():
    req = _req(response_format={"type": "json"}, reasoning={"effort": "low"}, cache_hints=[{"at": 0}])
    feats = {s.feature for s in preflight_substitutions(_profile({}), req)}
    assert feats == {"structured_output", "reasoning_control", "prompt_caching"}


def test_all_actions_are_valid():
    req = _req(response_format={"type": "json"}, tool_choice={"name": "t"},
              reasoning={"effort": "x"}, cache_hints=[{"a": 1}])
    for s in preflight_substitutions(_profile({}), req):
        assert s.action in VALID_ACTIONS
