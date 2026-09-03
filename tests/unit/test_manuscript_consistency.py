from engine.event import commit_event
from engine.manuscript import FactClaim, FactClaimKind
from engine.manuscript_consistency import check_manuscript_revision
from engine.manuscript_writer import DeterministicManuscriptWriter
from examples.huarong_lane.scenario import LIN, NIGHT, OUTER_ROBE
from world_schema import DialogueLine, Operation, OperationKind, StatePatch


def _transfer_revision(snapshot):
    event, new_state = commit_event(
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
    )
    revision = DeterministicManuscriptWriter(events_per_passage=1).write(
        [event], new_state
    )
    return event, new_state, revision


def test_grounded_revision_passes(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)

    result = check_manuscript_revision(revision, [event], new_state)

    assert result.valid, result.why()


def test_claim_cannot_invent_item_owner(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].fact_claims = [
        FactClaim(
            claim_id="invented_owner",
            kind=FactClaimKind.item_owner,
            source_event_ids=[event.event_id],
            subject_id=OUTER_ROBE,
            object_id=LIN,
            operation_kind=OperationKind.transfer_item,
            statement="外衫被交给林护卫。",
        )
    ]

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "claim_operation_grounding" in result.why()


def test_historical_rewrite_can_skip_latest_state_claim_check(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    new_state.items[OUTER_ROBE].owner_id = LIN

    strict = check_manuscript_revision(revision, [event], new_state)
    historical = check_manuscript_revision(
        revision,
        [event],
        new_state,
        validate_current_state=False,
    )

    assert not strict.valid
    assert "claim_state_consistency" in strict.why()
    assert historical.valid, historical.why()


def test_passage_version_range_must_match_sources(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].to_world_version = 99

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "passage_version_range" in result.why()


def test_dead_character_cannot_speak(snapshot):
    killed, new_state = commit_event(
        snapshot,
        action_id="fatal_blow",
        event_type="attack",
        patch=StatePatch(
            operations=[
                Operation(op=OperationKind.kill_character, target_id=LIN)
            ]
        ),
        actor_ids=[NIGHT],
        target_ids=[LIN],
    )
    revision = DeterministicManuscriptWriter(events_per_passage=1).write(
        [killed], new_state
    )
    revision.passages[0].dialogues = [
        DialogueLine(speaker_id=LIN, line="我还没有输。")
    ]

    result = check_manuscript_revision(revision, [killed], new_state)

    assert not result.valid
    assert "speaker_alive" in result.why()


def test_unknown_referenced_entity_is_rejected(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].referenced_entity_ids.append("char_not_exists")

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "referenced_entity_exists" in result.why()


def test_paragraphs_and_text_must_be_one_identical_reader_body(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].paragraphs = ["夜轻歌收回外衫。"]
    revision.passages[0].text = "夜轻歌收回外衫。\n\n另有一段只藏在 text 中。"

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "reader_body_unique" in result.why()


def test_exact_internal_tokens_are_rejected_but_normal_english_is_allowed(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].paragraphs = [
        "Alice 在 canonical.hall_open 前看见 char_hidden 执行 set_flag 后离开。"
    ]
    revision.passages[0].text = revision.passages[0].paragraphs[0]

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "internal_token_exposed" in result.why()

    revision.passages[0].paragraphs = [
        "Alice checked her ability to remain calm before the ceremony."
    ]
    revision.passages[0].text = revision.passages[0].paragraphs[0]
    allowed = check_manuscript_revision(revision, [event], new_state)

    assert allowed.valid, allowed.why()


def test_adjacent_long_paragraph_duplicates_are_rejected(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    long_paragraph = (
        "夜色压在院墙上，风从廊下卷过，灯影在青石地面来回晃动。"
        "夜轻歌抬手收好外衫，没有回头，只将所有惊疑都留在身后。"
        "远处脚步渐渐停住，院中一时无人开口。"
    )
    revision.passages[0].paragraphs = [long_paragraph, long_paragraph]
    revision.passages[0].text = "\n\n".join(revision.passages[0].paragraphs)

    result = check_manuscript_revision(revision, [event], new_state)

    assert not result.valid
    assert "adjacent_paragraph_duplicate" in result.why()


def test_short_adjacent_repetition_is_not_rejected(snapshot):
    event, new_state, revision = _transfer_revision(snapshot)
    revision.passages[0].paragraphs = ["她停下。", "她停下。"]
    revision.passages[0].text = "\n\n".join(revision.passages[0].paragraphs)

    result = check_manuscript_revision(revision, [event], new_state)

    assert result.valid, result.why()
