"""小说世界编译器测试。

策略：
- 确定性层 (text_loader / registry / scene_compiler / draft ops) 全程 mock，0 烧 token
- 真实 LLM 抽取用 @pytest.mark.llm 单独跑 (编译前 1-2 章)

覆盖 plan 第十二步 A (单场景) + B (单章节)。
"""

import json
from unittest import mock

import pytest

from compiler import (
    Chapter,
    EntityExtractor,
    EntityRegistry,
    PackageBuilder,
    VolumeCompiler,
    SceneCompiler,
    ChapterCompiler,
    SceneExtraction,
    RawEntity,
    RawEvent,
    RawCharacterState,
    RawForeshadow,
    RawGoalEvolution,
    RawRelation,
    RawWorldRule,
    load_novel,
    split_chapters,
    split_scenes,
    clean_text,
    compile_novel,
)
from world_schema import WorldState
from world_schema.models import OperationKind


# ---------------------------------------------------------------------------
# text_loader (确定性)
# ---------------------------------------------------------------------------


SAMPLE_TXT = """『第一狂妃：废柴三小姐/作者:豆娘』
『内容简介: 略』
------章节内容开始-------
第1章 华容巷
　　北月国，华容巷前聚集诸多看热闹的人。
　　"诶，这是哪家的小姐，竟这般不知羞耻。"
　　夜轻歌扶着墙面，摇摇晃晃的站起来。
第2章 那就脱！
　　"姐姐，你说什么呢。"夜清清呆愣住。
　　"把外衫脱下来。"夜轻歌忽然道。
　　翌日清晨，夜轻歌回到夜府。
"""


class TestTextLoader:
    def test_clean_text_normalizes_newlines(self):
        out = clean_text("a\r\nb\r\nc")
        assert "\r" not in out

    def test_clean_text_strips_ad_lines(self):
        out = clean_text("正文一行\n本书来自笔趣阁下载APP\n另一行")
        assert "笔趣阁" not in out
        assert "正文一行" in out

    def test_split_chapters_finds_headings(self):
        chs = split_chapters(SAMPLE_TXT)
        assert len(chs) == 2
        assert chs[0].index == 1
        assert chs[0].raw_number == "1"
        assert "华容巷" in chs[0].title
        assert chs[1].index == 2
        assert "那就脱" in chs[1].title

    def test_chapter_paragraphs_extracted(self):
        chs = split_chapters(SAMPLE_TXT)
        assert len(chs[0].paragraphs) >= 3
        # 段落不带前导全角空格
        assert all(not p.startswith("\u3000") for p in chs[0].paragraphs)

    def test_split_scenes_time_boundary(self):
        # 构造足够长的文本 (每段 >400 字) 让场景不被合并
        p1 = "夜轻歌站在华容巷，冷冷望着围观的人群。" * 30  # ~570 字
        p2 = "翌日清晨，夜轻歌回到夜府，独自盘算着后续。" * 30  # ~570 字
        ch = Chapter(index=2, raw_number="2", title="那就脱",
                     content=f"　　{p1}\n　　{p2}",
                     paragraphs=[p1, p2])
        # 有"翌日清晨"切换词且两段都够长，应切成 >=2 个场景
        scenes = split_scenes(ch)
        assert len(scenes) >= 2
        assert scenes[0].chapter_index == 2
        assert scenes[0].scene_index == 1

    def test_chapter_id_and_heading(self):
        chs = split_chapters(SAMPLE_TXT)
        assert chs[0].chapter_id == "ch_0001"
        assert "第1章" in chs[0].heading


# ---------------------------------------------------------------------------
# EntityRegistry (实体消歧)
# ---------------------------------------------------------------------------


class TestEntityRegistry:
    def test_alias_disambiguation(self):
        reg = EntityRegistry()
        c1 = reg.resolve_or_register_character(
            RawEntity(raw_name="夜轻歌", aliases=["三小姐", "废柴三小姐"]))
        c2 = reg.resolve_or_register_character(RawEntity(raw_name="三小姐"))
        assert c1 == c2
        assert reg.alias_index["夜轻歌"] == c1
        assert reg.alias_index["三小姐"] == c1

    def test_different_entities_get_different_ids(self):
        reg = EntityRegistry()
        a = reg.resolve_or_register_character(RawEntity(raw_name="夜轻歌"))
        b = reg.resolve_or_register_character(RawEntity(raw_name="夜清清"))
        assert a != b

    def test_merge_accumulates_aliases(self):
        reg = EntityRegistry()
        cid = reg.resolve_or_register_character(
            RawEntity(raw_name="夜轻歌", aliases=["三小姐"], identity_tags=["嫡系"]))
        # 第二次出现补充新别名/标签
        reg.resolve_or_register_character(
            RawEntity(raw_name=cid, canonical_id=cid,
                      aliases=["轻歌"], identity_tags=["废柴"]))
        ch = reg.characters[cid]
        assert "轻歌" in ch.aliases
        assert "废柴" in ch.identity_tags

    def test_known_entities_for_extractor(self):
        reg = EntityRegistry()
        reg.resolve_or_register_character(
            RawEntity(raw_name="夜轻歌", aliases=["三小姐"]))
        known = reg.known_entities()
        assert "夜轻歌" in known
        assert "三小姐" in known

    def test_resolve_name_only_lookup(self):
        reg = EntityRegistry()
        cid = reg.resolve_or_register_character(RawEntity(raw_name="林管家"))
        assert reg.resolve_name("林管家") == cid
        assert reg.resolve_name("查无此人") is None


# ---------------------------------------------------------------------------
# SceneCompiler (mock extraction)
# ---------------------------------------------------------------------------


def _make_extraction(scene_id="sc1") -> SceneExtraction:
    return SceneExtraction(
        scene_id=scene_id,
        summary="夜轻歌被当众羞辱，夜清清陷害",
        entities=[
            RawEntity(raw_name="夜轻歌", entity_type="character",
                      aliases=["三小姐"], identity_tags=["嫡系", "废柴"],
                      description="半脸紫红胎记", confidence=0.9),
            RawEntity(raw_name="夜清清", entity_type="character",
                      aliases=["庶妹"], identity_tags=["庶出"],
                      confidence=0.9),
            RawEntity(raw_name="华容巷", entity_type="location",
                      confidence=0.8),
            RawEntity(raw_name="外衫", entity_type="item",
                      confidence=0.7),
        ],
        relations=[
            RawRelation(source_name="夜清清", target_name="夜轻歌",
                        public_relation="庶妹-嫡姐",
                        private_relation="嫉妒陷害",
                        dimensions={"hostility": 0.7, "affection": -0.5},
                        confidence=0.8),
        ],
        events=[
            RawEvent(summary="夜轻歌被诬通奸受辱", event_type="conflict",
                     actor_names=["夜清清"], target_names=["夜轻歌"],
                     patch_operations=[
                         {"op": "set_flag", "path": "plot.shaming_happened",
                          "value": True},
                     ],
                     confidence=0.85, order=1),
            RawEvent(summary="夜轻歌拿走外衫", event_type="transfer",
                     actor_names=["夜轻歌"], target_names=["外衫"],
                     patch_operations=[
                         {"op": "transfer_item", "item_id": "外衫",
                          "target_id": "夜轻歌"},
                     ],
                     confidence=0.8, order=2),
        ],
        world_rules=[
            RawWorldRule(category="politics",
                         statement="庶出不可忤逆嫡系",
                         confidence=0.7),
        ],
    )


class TestSceneCompiler:
    def test_compile_registers_entities_and_relations(self):
        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        sc = SceneCompiler()
        result = sc.compile(_make_extraction(), reg, state)

        # 4 个实体注册进 state
        assert len(state.characters) == 2
        assert len(state.locations) == 1
        assert len(state.items) == 1
        # 关系
        assert len(state.relations) == 1
        rel = state.relations[0]
        assert rel.public_relation == "庶妹-嫡姐"
        assert rel.dimensions.hostility > 0.5
        # 世界规则
        assert len(state.world_rules) == 1
        # 事件 patch: set_flag + transfer_item (transfer 经实体消歧后合法)
        kinds = {op.op for op in result.applied_patch.operations}
        assert OperationKind.set_flag in kinds
        assert OperationKind.transfer_item in kinds

    def test_transfer_item_resolved_to_canonical_id(self):
        """草稿里 item_id="外衫" (中文名) 应被解析成 registry 里的稳定 id。"""
        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        sc = SceneCompiler()
        sc.compile(_make_extraction(), reg, state)
        # 找到 transfer op
        transfer = None
        # 再编一次拿 patch
        result = sc.compile(_make_extraction(), reg, state)
        for op in result.applied_patch.operations:
            if op.op == OperationKind.transfer_item:
                transfer = op
                break
        assert transfer is not None
        # item_id 应是注册后的 id (不是中文"外衫")
        assert transfer.item_id in state.items

    def test_fabricated_op_dropped(self):
        """草稿里引用未知实体的 op 应被丢弃。"""
        ext = _make_extraction()
        ext.events.append(RawEvent(
            summary="x", actor_names=["夜轻歌"],
            patch_operations=[
                {"op": "transfer_item", "item_id": "不存在的物品",
                 "target_id": "夜轻歌"},
            ],
            order=3,
        ))
        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        sc = SceneCompiler()
        result = sc.compile(ext, reg, state)
        # 该 op 因 item 不存在被 patch_validator 丢
        transfers = [op for op in result.applied_patch.operations
                     if op.op == OperationKind.transfer_item]
        # 只剩合法的那个 (外衫)
        assert all(op.item_id in state.items for op in transfers)


# ---------------------------------------------------------------------------
# ChapterCompiler (mock extractor)
# ---------------------------------------------------------------------------


class TestChapterCompiler:
    def test_multi_scene_compiles_with_disambiguation(self):
        """两个场景都出现"夜轻歌"，应消歧成同一 id。

        章节正文要足够长，让 split_scenes 产出 2 个场景 (每段 >400 字)。
        """
        p1 = "夜轻歌站在华容巷，神色冷峻地望着围观人群。" * 30
        p2 = "翌日清晨，三小姐回到夜府，盘算着后续对策。" * 30
        ch = Chapter(index=1, raw_number="1", title="测试章",
                     content=f"　　{p1}\n　　{p2}",
                     paragraphs=[p1, p2])

        # mock extractor: 两个场景各返回一份抽取
        ext1 = _make_extraction("ch0001_sc01")
        ext2 = SceneExtraction(
            scene_id="ch0001_sc02",
            summary="三小姐回到夜府",
            entities=[RawEntity(raw_name="三小姐", entity_type="character",
                                confidence=0.9)],  # 用别名，应命中
            relations=[], events=[], world_rules=[],
        )
        ext = mock.Mock()
        ext.extract = mock.Mock(side_effect=[ext1, ext2])

        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        cc = ChapterCompiler(extractor=ext)
        result = cc.compile(ch, reg, state)

        assert result.extraction_count == 2
        # 场景1 抽出 夜轻歌+夜清清 (2角色)，场景2 的"三小姐"应命中夜轻歌
        # 所以最终仍为 2 个角色，且"三小姐"合并进夜轻歌的别名
        assert len(state.characters) == 2
        yqs = [c for c in state.characters.values()
               if "夜轻歌" in c.display_name or "夜轻歌" in c.aliases]
        assert yqs, "夜轻歌未注册"
        assert "三小姐" in yqs[0].aliases

    def test_extraction_failure_recorded_as_warning(self):
        ch = Chapter(index=1, raw_number="1", title="x",
                     content="x", paragraphs=["x"])
        ext = mock.Mock()
        ext.extract = mock.Mock(return_value=None)
        ext.last_error = "network"
        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        cc = ChapterCompiler(extractor=ext)
        result = cc.compile(ch, reg, state)
        assert result.extraction_count == 0
        assert result.warnings  # 有告警


# ---------------------------------------------------------------------------
# VolumeCompiler (C 阶段：跨章节演化)
# ---------------------------------------------------------------------------


class TestVolumeCompiler:
    def test_cross_chapter_state_foreshadow_and_goal_evolution(self):
        chapter_one = Chapter(
            index=1,
            raw_number="1",
            title="埋下疑云",
            content="夜轻歌发现毒茶有异。",
            paragraphs=["夜轻歌发现毒茶有异。"],
        )
        chapter_two = Chapter(
            index=2,
            raw_number="2",
            title="真相浮现",
            content="三小姐查明毒茶来自夜清清。",
            paragraphs=["三小姐查明毒茶来自夜清清。"],
        )
        extraction_one = SceneExtraction(
            scene_id="ch0001_sc01",
            summary="夜轻歌发现毒茶疑点",
            entities=[
                RawEntity(
                    raw_name="夜轻歌",
                    aliases=["三小姐"],
                ),
                RawEntity(raw_name="夜清清"),
            ],
            character_states=[
                RawCharacterState(
                    character_name="夜轻歌",
                    state_summary="开始警惕毒茶来源",
                    emotion="警觉",
                    evidence="茶味不对",
                    confidence=0.9,
                )
            ],
            foreshadows=[
                RawForeshadow(
                    title="毒茶真相",
                    description="毒茶来源不明",
                    status="planted",
                    related_names=["夜轻歌", "夜清清"],
                    evidence="茶味不对",
                    confidence=0.85,
                )
            ],
            goal_evolutions=[
                RawGoalEvolution(
                    character_name="夜轻歌",
                    goal_key="investigate_poison",
                    description="查明毒茶来源",
                    status="active",
                    priority=0.9,
                    target_names=["夜清清"],
                    evidence="她决定暗查",
                    confidence=0.8,
                )
            ],
        )
        extraction_two = SceneExtraction(
            scene_id="ch0002_sc01",
            summary="夜轻歌查明下毒者",
            entities=[
                RawEntity(
                    raw_name="三小姐",
                    canonical_id="",
                )
            ],
            character_states=[
                RawCharacterState(
                    character_name="三小姐",
                    state_summary="掌握夜清清下毒证据",
                    emotion="冷静",
                    evidence="证据指向夜清清",
                    confidence=0.9,
                )
            ],
            foreshadows=[
                RawForeshadow(
                    title="毒茶真相",
                    description="毒茶由夜清清安排",
                    status="resolved",
                    related_names=["三小姐", "夜清清"],
                    evidence="证据指向夜清清",
                    confidence=0.9,
                )
            ],
            goal_evolutions=[
                RawGoalEvolution(
                    character_name="三小姐",
                    goal_key="investigate_poison",
                    description="查明毒茶来源",
                    status="achieved",
                    priority=0.9,
                    target_names=["夜清清"],
                    evidence="已取得证据",
                    confidence=0.9,
                )
            ],
        )
        extractor = mock.Mock()
        extractor.extract = mock.Mock(
            side_effect=[extraction_one, extraction_two]
        )
        registry = EntityRegistry()
        state = WorldState(timeline_id="volume")

        result = VolumeCompiler(extractor=extractor).compile(
            [chapter_two, chapter_one],
            registry,
            state,
        )

        night_id = registry.resolve_name("夜轻歌")
        assert registry.resolve_name("三小姐") == night_id
        history = state.characters[night_id].attrs["chapter_states"]
        assert [item["chapter"] for item in history] == [1, 2]
        foreshadows = [
            arc for arc in state.plot.values()
            if arc.kind == "foreshadow"
        ]
        assert len(foreshadows) == 1
        assert foreshadows[0].completed is True
        assert foreshadows[0].attrs["resolved_chapter"] == 2
        goals = state.character_psyches[night_id].goals
        assert len(goals) == 1
        assert goals[0].achieved is True
        assert len(goals[0].__dict__["evolution"]) == 2
        assert result.source_chapters == [1, 2]
        assert result.manifest()["stage"] == "C"


# ---------------------------------------------------------------------------
# PackageBuilder
# ---------------------------------------------------------------------------


class TestPackageBuilder:
    def test_build_package_serializable(self, tmp_path):
        reg = EntityRegistry()
        state = WorldState(timeline_id="t1")
        SceneCompiler().compile(_make_extraction(), reg, state)
        pkg = PackageBuilder().build(
            package_id="test", novel="测试小说",
            source_chapters=[1], state=state, registry=reg,
        )
        assert pkg.package_id == "test"
        assert pkg.manifest["character_count"] == 2
        out = tmp_path / "pkg.json"
        pkg.save(str(out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["package_id"] == "test"
        assert "snapshot" in data
        assert len(data["snapshot"]["characters"]) == 2


# ---------------------------------------------------------------------------
# compile_novel 端到端 (mock extractor)
# ---------------------------------------------------------------------------


class TestCompileNovel:
    def test_end_to_end_mocked(self, tmp_path):
        """用真实 text_loader + mock extractor 跑端到端编译。"""
        novel = tmp_path / "novel.txt"
        novel.write_text(SAMPLE_TXT, encoding="utf-8")

        ext = mock.Mock()
        ext.extract = mock.Mock(
            return_value=_make_extraction("mocked_scene"))
        pkg = compile_novel(
            str(novel), chapters=[1],
            package_id="mock_pkg", extractor=ext,
        )
        assert pkg.source_chapters == [1]
        assert len(pkg.snapshot.characters) == 2
        # extractor 被调用 (至少 1 个场景)
        assert ext.extract.called


# ---------------------------------------------------------------------------
# 真实 LLM 编译 (默认跳过)
# ---------------------------------------------------------------------------


@pytest.mark.llm
class TestRealLLMCompile:
    def test_real_compile_first_chapter(self, tmp_path):
        """真实 LLM 编译《第一狂妃》第 1 章，验证能产出非空 WorldPackage。"""
        pkg = compile_novel(
            "novels/第一狂妃：废柴三小姐.txt",
            chapters=[1],
            package_id="huarong_lane_compiled",
            novel_name="第一狂妃：废柴三小姐",
        )
        print(f"\n{'='*60}")
        print(f"[编译完成] {pkg.package_id}")
        print(f"  角色 ({len(pkg.snapshot.characters)}):")
        for c in pkg.snapshot.characters.values():
            print(f"    - {c.character_id} | {c.display_name} | {c.aliases} | {c.identity_tags}")
        print(f"  关系 ({len(pkg.snapshot.relations)}):")
        for r in pkg.snapshot.relations:
            print(f"    - {r.source_id} -> {r.target_id} | {r.public_relation}")
        print(f"  世界规则 ({len(pkg.snapshot.world_rules)}):")
        for wr in pkg.snapshot.world_rules:
            print(f"    - [{wr.category}] {wr.statement}")
        print(f"  flags: {pkg.snapshot.flags}")
        print(f"{'='*60}")

        # 至少抽到主角夜轻歌
        all_names = []
        for c in pkg.snapshot.characters.values():
            all_names.append(c.display_name)
            all_names.extend(c.aliases)
        assert any("夜轻歌" in n or "三小姐" in n for n in all_names), \
            "主角夜轻歌未被抽出"
        assert len(pkg.snapshot.characters) >= 2  # 至少两角色

    def test_real_two_chapter_volume_tracks_story_evolution(self, tmp_path):
        """真实编译小型双章节样本，验证 C 阶段跨章演化而不加载整部长篇。"""

        novel = tmp_path / "two_chapter_evolution.txt"
        novel.write_text(
            """------章节内容开始-------
第1章 毒茶疑云

夜轻歌在宴席上发现茶水里藏有慢性毒药。她表面镇定，内心由警惕转为愤怒。
她立下明确目标：查明毒茶幕后主使，并决定暂时隐瞒中毒线索。
临走时，她注意到夜清清袖口沾着同样的药粉；这条药粉线索成为尚未揭晓的伏笔。

第2章 药粉真相

夜轻歌追查药粉来源，确认夜清清就是毒茶幕后主使。
前章的袖口药粉伏笔在对质中得到回收，她也公开了自己早已识破毒茶。
夜轻歌从愤怒转为冷静，完成“查明毒茶幕后主使”的目标，并开始保护下一场宴席。
""",
            encoding="utf-8",
        )

        pkg = compile_novel(
            str(novel),
            chapters=[1, 2],
            package_id="two_chapter_volume_smoke",
            novel_name="毒茶疑云",
        )
        compiler_meta = pkg.manifest["compiler"]
        update_count = (
            compiler_meta["character_state_updates"]
            + compiler_meta["foreshadow_updates"]
            + compiler_meta["goal_updates"]
        )

        assert compiler_meta["stage"] == "C"
        assert compiler_meta["source_chapters"] == [1, 2]
        assert len(compiler_meta["chapter_summaries"]) == 2
        assert update_count > 0
