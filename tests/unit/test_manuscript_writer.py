import pytest

from engine.event import commit_event
from engine.manuscript import ManuscriptSource
from engine.manuscript_writer import (
    DeterministicManuscriptWriter,
    LLMManuscriptWriter,
    ManuscriptWriterError,
    narrative_output_to_revision,
)
from examples.huarong_lane.scenario import NIGHT, OUTER_ROBE, QINGQING
from world_schema import DialogueLine, NarrativeOutput, Operation, OperationKind, StatePatch


def _transfer_event(snapshot):
    return commit_event(
        snapshot,
        action_id="take_robe",
        event_type="swap_object",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.transfer_item,
                    item_id=OUTER_ROBE,
                    target_id=NIGHT,
                )
            ]
        ),
        actor_ids=[NIGHT],
        target_ids=[OUTER_ROBE],
        summary="夜轻歌拿回外衫",
    )


def test_deterministic_writer_groups_continuous_events(snapshot):
    first, state_v1 = _transfer_event(snapshot)
    second, state_v2 = commit_event(
        state_v1,
        action_id="raise_guard",
        event_type="world_change",
        patch=StatePatch(
            operations=[
                Operation(op=OperationKind.set_flag, path="courtyard.alert", value=True)
            ]
        ),
        actor_ids=[QINGQING],
        summary="院中戒备骤然收紧",
    )

    revision = DeterministicManuscriptWriter(events_per_passage=2).write(
        [first, second],
        state_v2,
        chapter_number=2,
    )

    assert revision.source == ManuscriptSource.deterministic
    assert revision.source_event_ids == [first.event_id, second.event_id]
    assert len(revision.passages) == 1
    passage = revision.passages[0]
    assert passage.source_event_ids == [first.event_id, second.event_id]
    assert passage.from_world_version == 1
    assert passage.to_world_version == 2
    assert passage.text == "\n\n".join(passage.paragraphs)
    assert len(passage.paragraphs) == 2
    assert passage.input_hash
    assert passage.writer_version == "deterministic.v1"
    assert {claim.operation_kind for claim in passage.fact_claims} == {
        OperationKind.transfer_item,
        OperationKind.set_flag,
    }


def test_internal_events_keep_lineage_without_becoming_prose(snapshot):
    first, state_v1 = _transfer_event(snapshot)
    internal, state_v2 = commit_event(
        state_v1,
        action_id="dialogue_perceived",
        event_type="system.dialogue_perceived",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="system.perception_recorded",
                    value=True,
                )
            ]
        ),
        actor_ids=[QINGQING],
        summary="系统记录了对白感知",
    )

    revision = DeterministicManuscriptWriter(events_per_passage=2).write(
        [first, internal],
        state_v2,
    )

    passage = revision.passages[0]
    assert passage.source_event_ids == [first.event_id, internal.event_id]
    assert "外衫" in passage.text
    assert "system.perception_recorded" not in passage.text
    assert {claim.operation_kind for claim in passage.fact_claims} == {
        OperationKind.transfer_item,
        OperationKind.set_flag,
    }


def test_existing_narrative_becomes_ready_revision_without_model(snapshot):
    event, new_state = _transfer_event(snapshot)
    narrative = NarrativeOutput(
        narration="风掠过院墙，夜轻歌抬手将外衫收回臂弯。",
        dialogues=[
            DialogueLine(
                speaker_id=NIGHT,
                to_id=QINGQING,
                line="这件东西，本就不该在你手里。",
                tone="冷淡",
            )
        ],
        system_hints=["夜轻歌重新持有外衫"],
        grounded_event_ids=[event.event_id],
        referenced_entity_ids=[NIGHT, QINGQING, OUTER_ROBE],
    )

    revision = narrative_output_to_revision(narrative, [event], new_state)

    passage = revision.passages[0]
    assert revision.source == ManuscriptSource.narrative_output
    assert passage.paragraphs == [
        narrative.narration,
        "夜轻歌说：“这件东西，本就不该在你手里。”",
    ]
    assert passage.text == "\n\n".join(passage.paragraphs)
    assert passage.dialogues == narrative.dialogues
    assert passage.system_hints == narrative.system_hints
    assert passage.viewpoint == "third_person"
    assert passage.from_world_version == event.new_version
    assert passage.to_world_version == event.new_version
    assert passage.current_revision == 1


def test_deterministic_writer_prefers_presentation_and_hides_internal_operations(snapshot):
    event, new_state = commit_event(
        snapshot,
        action_id="presented_turn",
        event_type="world_change",
        patch=StatePatch(
            operations=[
                Operation(
                    op=OperationKind.set_flag,
                    path="canonical.secret_opened",
                    value={"raw": ["char_missing"]},
                    reason="ability:mind_reading",
                ),
                Operation(
                    op=OperationKind.increment_value,
                    path="plot.hidden_score",
                    delta=1,
                ),
            ]
        ),
        actor_ids=[NIGHT],
        summary="char_missing used ability:mind_reading",
        presentation_events=[
            {
                "event_type": "narration",
                "payload": {"text": "风掠过院墙，夜轻歌忽然停下脚步。"},
            },
            {
                "event_type": "dialogue",
                "payload": {
                    "speaker_id": NIGHT,
                    "to_id": QINGQING,
                    "line": "今晚别再跟来。",
                },
            },
        ],
    )

    passage = DeterministicManuscriptWriter().write([event], new_state).passages[0]

    assert passage.paragraphs == [
        "风掠过院墙，夜轻歌忽然停下脚步。",
        "夜轻歌说：“今晚别再跟来。”",
    ]
    assert passage.text == "\n\n".join(passage.paragraphs)
    assert "canonical." not in passage.text
    assert "ability:" not in passage.text
    assert "char_missing" not in passage.text
    assert "承接前事" not in passage.text
    assert "随后" not in passage.text
    assert {claim.operation_kind for claim in passage.fact_claims} == {
        OperationKind.set_flag,
        OperationKind.increment_value,
    }


def test_deterministic_writer_uses_chinese_generic_for_unknown_ids(snapshot):
    event, new_state = _transfer_event(snapshot)
    event.patch = StatePatch(
        operations=[
            Operation(
                op=OperationKind.move_character,
                target_id="char_missing",
                location_id="loc_missing",
            )
        ]
    )

    passage = DeterministicManuscriptWriter().write([event], new_state).passages[0]

    assert "那人来到那处地方" in passage.text
    assert "char_missing" not in passage.text
    assert "loc_missing" not in passage.text


def test_llm_writer_accepts_one_presentation_passage_and_fills_server_fields(snapshot):
    event, new_state = _transfer_event(snapshot)
    calls = []

    def fake_provider(events, state, chapter_number, previous_passage):
        calls.append((events, state, chapter_number, previous_passage))
        return {
            "passage": {
                "paragraphs": [
                    "夜轻歌将外衫收回臂弯。",
                    "夜轻歌说：“东西该还我了。”",
                ],
                "referenced_entity_ids": [NIGHT, OUTER_ROBE],
                "dialogues": [
                    {"speaker_id": NIGHT, "line": "东西该还我了。"}
                ],
                "viewpoint": "third_person",
            }
        }

    revision = LLMManuscriptWriter(generator=fake_provider).write(
        [event], new_state, chapter_number=3
    )
    passage = revision.passages[0]

    assert len(calls) == 1
    assert len(revision.passages) == 1
    assert passage.text == "\n\n".join(passage.paragraphs)
    assert passage.source_event_ids == [event.event_id]
    assert passage.from_world_version == event.new_version
    assert passage.to_world_version == event.new_version
    assert passage.fact_claims[0].operation_kind == OperationKind.transfer_item
    assert passage.input_hash == passage.source_fingerprint
    assert passage.writer_version == "llm.v2"
    assert passage.generation_kind == ManuscriptSource.llm
    assert passage.current_revision == 1
    assert revision.writer_version == "llm.v2"
    assert revision.input_hash == passage.input_hash


def test_llm_writer_repairs_once_with_fake_provider(snapshot):
    event, new_state = _transfer_event(snapshot)
    attempts = iter(
        [
            {"passages": []},
            {"passage": {"paragraphs": ["夜轻歌收回了外衫。"]}},
        ]
    )

    def fake_provider(events, state, chapter_number, previous_passage):
        return next(attempts)

    revision = LLMManuscriptWriter(
        generator=fake_provider,
        repair_attempts=1,
    ).write([event], new_state)

    assert revision.passages[0].text == "夜轻歌收回了外衫。"


def test_llm_writer_rejects_server_owned_fields_without_provider_access(snapshot):
    event, new_state = _transfer_event(snapshot)

    def fake_provider(events, state, chapter_number, previous_passage):
        return {
            "passage": {
                "paragraphs": ["夜轻歌收回了外衫。"],
                "source_event_ids": [event.event_id],
            }
        }

    with pytest.raises(ManuscriptWriterError, match="server-owned"):
        LLMManuscriptWriter(
            generator=fake_provider,
            repair_attempts=0,
        ).write([event], new_state)
