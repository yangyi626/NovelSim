"""apply_patch 单元测试: 各种 Operation 的语义与边界。"""

import pytest

from engine import apply_patch, PatchError
from world_schema import Operation, OperationKind, StatePatch
from world_schema.models import Belief

from examples.huarong_lane.scenario import (
    LIN,
    NIGHT,
    OUTER_ROBE,
    QINGQING,
    SCENE_ID,
)


def _patch(*ops):
    return StatePatch(operations=list(ops))


def _op(**kw):
    return Operation(**kw)


# ---- set_flag / set_attr / increment_value ----

class TestFlags:
    def test_set_flag_flat(self, snapshot):
        new = apply_patch(
            snapshot,
            _patch(_op(op=OperationKind.set_flag, path="plot.shaming_resolved", value=True)),
        )
        assert new.flags["plot.shaming_resolved"] is True
        # 原状态不被修改
        assert "plot.shaming_resolved" not in snapshot.flags

    def test_set_flag_empty_path_rejected(self, snapshot):
        with pytest.raises(PatchError):
            apply_patch(
                snapshot, _patch(_op(op=OperationKind.set_flag, path="", value=True))
            )

    def test_set_attr_on_character(self, snapshot):
        new = apply_patch(
            snapshot,
            _patch(
                _op(
                    op=OperationKind.set_attr,
                    path=f"{NIGHT}.cultivation_level",
                    value="筑基初期",
                )
            ),
        )
        assert new.characters[NIGHT].attrs["cultivation_level"] == "筑基初期"

    def test_increment_clamps_relation_dimension(self, snapshot):
        # 敌意是 [0,1]，加 0.5 后再 +0.8 应被钳到 1.0
        p1 = _patch(
            _op(
                op=OperationKind.update_relation,
                source_id=NIGHT,
                target_id=QINGQING,
                dimension="hostility",
                delta=0.5,
            )
        )
        s1 = apply_patch(snapshot, p1)
        s2 = apply_patch(
            s1,
            _patch(
                _op(
                    op=OperationKind.update_relation,
                    source_id=NIGHT,
                    target_id=QINGQING,
                    dimension="hostility",
                    delta=0.8,
                )
            ),
        )
        assert s2.relations[0].dimensions.hostility == pytest.approx(1.0)


# ---- transfer_item / move_character ----

class TestSpatial:
    def test_transfer_outer_robe_to_night(self, snapshot):
        # 原著: 夜轻歌让夜清清脱下外衫并拿走
        new = apply_patch(
            snapshot,
            _patch(
                _op(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                )
            ),
        )
        robe = new.items[OUTER_ROBE]
        assert robe.owner_id == NIGHT
        assert OUTER_ROBE in new.characters[NIGHT].inventory
        assert OUTER_ROBE not in new.characters[QINGQING].inventory

    def test_move_character_updates_location(self, snapshot):
        new = apply_patch(
            snapshot,
            _patch(_op(op=OperationKind.move_character, target_id=NIGHT, location_id="loc_yefu")),
        )
        assert new.characters[NIGHT].location_id == "loc_yefu"

    def test_transfer_unknown_item_raises(self, snapshot):
        with pytest.raises(PatchError):
            apply_patch(
                snapshot,
                _patch(_op(op=OperationKind.transfer_item, item_id="no_such_item", target_id=NIGHT)),
            )


# ---- beliefs ----

class TestBeliefs:
    def test_update_belief_creates_if_missing(self, snapshot):
        # 林管家原本对下毒是 suspected，改成 believed_true
        new = apply_patch(
            snapshot,
            _patch(
                _op(
                    op=OperationKind.update_belief,
                    target_id=LIN,
                    fact_id="fact_qingqing_poisoned_tea",
                    belief=Belief.believed_true,
                    confidence=0.85,
                    source_type="inference",
                )
            ),
        )
        b = next(
            x for x in new.beliefs[LIN] if x.fact_id == "fact_qingqing_poisoned_tea"
        )
        assert b.belief == Belief.believed_true
        assert b.confidence == pytest.approx(0.85)


# ---- purity ----

class TestPurity:
    def test_apply_does_not_mutate_input(self, snapshot):
        original_hostility = snapshot.relations[0].dimensions.hostility
        apply_patch(
            snapshot,
            _patch(
                _op(
                    op=OperationKind.update_relation,
                    source_id=NIGHT,
                    target_id=QINGQING,
                    dimension="hostility",
                    delta=0.9,
                )
            ),
        )
        assert snapshot.relations[0].dimensions.hostility == original_hostility
