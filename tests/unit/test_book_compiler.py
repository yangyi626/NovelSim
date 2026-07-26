"""编译器 D：全书消歧、多时间线和三级快照。"""

from compiler import (
    BookCompiler,
    EntityRegistry,
    RawEntity,
    RawEvent,
    SceneExtraction,
)
from compiler.cli import _fresh_state
from compiler.text_loader import Chapter


def _chapter(index, title=""):
    return Chapter(
        index=index,
        raw_number=str(index),
        title=title or f"章节 {index}",
        content=f"第 {index} 章正文",
        paragraphs=[f"第 {index} 章正文"],
    )


class SequenceExtractor:
    def __init__(self, extractions):
        self.extractions = list(extractions)
        self.calls = 0
        self.model = "fake-book-model"
        self.last_error = ""

    def extract(self, text, *, scene_id, **kwargs):
        extraction = self.extractions[self.calls].copy(deep=True)
        extraction.scene_id = scene_id
        self.calls += 1
        return extraction


def _extraction(name, identity="", aliases=None):
    return SceneExtraction(
        scene_id="placeholder",
        summary=f"{name} 推进剧情",
        entities=[
            RawEntity(
                raw_name=name,
                aliases=aliases or [],
                global_identity=identity,
                incarnation=name,
                evidence=f"{name} 出场",
            )
        ],
        events=[
            RawEvent(
                summary=f"{name} 推进剧情",
                actor_names=[name],
                evidence=f"{name} 推进剧情",
            )
        ],
    )


def test_book_compiler_resolves_identity_across_timelines_and_snapshots():
    extractor = SequenceExtractor(
        [
            _extraction("无名佣兵", "soul_night", ["三小姐"]),
            _extraction("夜轻歌", "soul_night", ["三小姐"]),
            _extraction("夜轻歌", "soul_night"),
        ]
    )
    compiler = BookCompiler(extractor=extractor, volume_size=2)
    state = _fresh_state("book_d")
    registry = EntityRegistry()

    result = compiler.compile(
        [_chapter(1), _chapter(2), _chapter(3)],
        registry,
        state,
        timeline_plan={
            1: "timeline_origin",
            2: "timeline_novel",
            3: "timeline_novel",
        },
    )

    assert len(state.characters) == 1
    character = next(iter(state.characters.values()))
    assert len(character.attrs["book_occurrences"]) == 3
    assert {
        item["timeline_id"]
        for item in character.attrs["book_occurrences"]
    } == {"timeline_origin", "timeline_novel"}
    assert len(result.trajectory_events) == 3
    assert result.manifest()["stage"] == "D"
    assert result.manifest()["global_identity_count"] == 1
    assert [item.level for item in result.snapshots].count("chapter") == 3
    assert [item.level for item in result.snapshots].count("volume") == 2
    assert [item.level for item in result.snapshots].count("book") == 1
    assert set(result.timelines) == {
        "timeline_origin",
        "timeline_novel",
    }


def test_explicit_different_global_identities_do_not_merge_same_name():
    extractor = SequenceExtractor(
        [
            _extraction("阿宁", "identity_first"),
            _extraction("阿宁", "identity_second"),
        ]
    )
    state = _fresh_state("identity_conflict")
    registry = EntityRegistry()

    result = BookCompiler(extractor=extractor).compile(
        [_chapter(1), _chapter(2)],
        registry,
        state,
    )

    assert len(state.characters) == 2
    assert result.global_identity_count == 2
    assert result.ambiguous_alias_count == 1


def test_book_compiler_can_stop_between_chapters():
    extractor = SequenceExtractor(
        [_extraction("甲"), _extraction("乙")]
    )
    state = _fresh_state("interrupt")
    registry = EntityRegistry()
    completed = []

    result = BookCompiler(extractor=extractor).compile(
        [_chapter(1), _chapter(2)],
        registry,
        state,
        stop_requested=lambda: "paused" if completed else "",
        on_chapter_completed=lambda chapter, *_: completed.append(
            chapter.index
        ),
    )

    assert result.interrupted == "paused"
    assert result.source_chapters == [1]
    assert completed == [1]
    assert not any(item.level == "book" for item in result.snapshots)


def test_extracted_timeline_is_used_when_no_manual_plan():
    extraction = _extraction("时间旅人", "traveller")
    extraction.entities[0].timeline_id = "timeline_future"
    state = _fresh_state("detected_timeline")

    result = BookCompiler(
        extractor=SequenceExtractor([extraction])
    ).compile(
        [_chapter(1)],
        EntityRegistry(),
        state,
    )

    assert result.timelines == {"timeline_future": [1]}
    assert result.snapshots[0].timeline_ids == ["timeline_future"]
